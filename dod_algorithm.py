# -*- coding: utf-8 -*-

"""
/***************************************************************************
 DoDetector
                                 A QGIS plugin
 Terrain change detection tool using DEM of Difference (DoD) method.
                              -------------------
        begin                : 2026-03-05
        copyright            : (C) 2026 by Laboratory for Urban Boundaries
        email                : hello@lub.global
 ***************************************************************************/

DoDetector - Complete DoD Workflow:

    Step 1: Load inputs (single files or folders with tiles)
    Step 2: Build VRT if multiple tiles
    Step 3: Check CRS and reproject to match NEW DTM (e.g., 25832 -> 25833)
    Step 4: Calculate rectangular intersection extent
    Step 5: Clip and align BOTH rasters to common extent/resolution
    Step 6: Convert 0 values to NoData
    Step 7: Create valid data masks and intersect
    Step 8: Apply mask to get only common valid area
    Step 9: Calculate difference (New - Old)
    Step 10: Apply threshold filter
    Step 11: Report statistics and calculate TRI

Output interpretation:
    Positive values = terrain rose (fill, deposition, construction)
    Negative values = terrain lowered (cut, erosion, excavation)
"""

__author__ = 'Laboratory for Urban Boundaries'
__date__ = '2026-03-05'
__copyright__ = '(C) 2026 by Laboratory for Urban Boundaries'
__revision__ = '$Format:%H$'

import os
import glob

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFile,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterString,
    QgsProcessingException,
    QgsProcessingMultiStepFeedback,
    QgsRasterLayer,
    QgsCoordinateReferenceSystem,
    QgsRectangle
)
import processing


class DoDetectorAlgorithm(QgsProcessingAlgorithm):
    
    # Parameter constants
    INPUT_MODE = 'INPUT_MODE'
    DTM_OLD_FILE = 'DTM_OLD_FILE'
    DTM_NEW_FILE = 'DTM_NEW_FILE'
    DTM_OLD_FOLDER = 'DTM_OLD_FOLDER'
    DTM_NEW_FOLDER = 'DTM_NEW_FOLDER'
    FILE_PATTERN = 'FILE_PATTERN'
    NODATA_VALUE = 'NODATA_VALUE'
    APPLY_THRESHOLD = 'APPLY_THRESHOLD'
    THRESHOLD_VALUE = 'THRESHOLD_VALUE'
    OUTPUT = 'OUTPUT'
    
    INPUT_MODES = ['Single raster files', 'Folders with multiple DTM tiles']
    
    def tr(self, string):
        return QCoreApplication.translate('Processing', string)
    
    def createInstance(self):
        return DoDetectorAlgorithm()
    
    def name(self):
        return 'calculate_dod'
    
    def displayName(self):
        return self.tr('Calculate DoD')
    
    def group(self):
        return self.tr('Terrain Change Detection')
    
    def groupId(self):
        return 'terrainchangedetection'
    
    def shortHelpString(self):
        return self.tr("""
DoDetector - DEM of Difference Analysis

WORKFLOW (11 Steps):
1. Load inputs (files or folders)
2. Build VRT if multiple tiles
3. Reproject OLD to match NEW CRS
4. Calculate intersection extent
5. Clip and align both rasters
6. Convert 0 to NoData
7. Create and apply valid data mask
8. Mask invalid areas
9. Calculate DoD (New - Old)
10. Apply threshold
11. Statistics and TRI

OUTPUT:
- DoD raster (positive=fill, negative=cut)
- TRI raster (terrain ruggedness)

Developed by Laboratory for Urban Boundaries (LUB)
https://dirtybusiness.no
        """)
    
    def initAlgorithm(self, config=None):
        
        self.addParameter(
            QgsProcessingParameterEnum(
                self.INPUT_MODE,
                self.tr('Input mode'),
                options=self.INPUT_MODES,
                defaultValue=0
            )
        )
        
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.DTM_OLD_FILE,
                self.tr('Old/Reference DTM (single file)'),
                optional=True
            )
        )
        
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.DTM_NEW_FILE,
                self.tr('New/Comparison DTM (single file)'),
                optional=True
            )
        )
        
        self.addParameter(
            QgsProcessingParameterFile(
                self.DTM_OLD_FOLDER,
                self.tr('Old/Reference DTM folder'),
                behavior=QgsProcessingParameterFile.Folder,
                optional=True
            )
        )
        
        self.addParameter(
            QgsProcessingParameterFile(
                self.DTM_NEW_FOLDER,
                self.tr('New/Comparison DTM folder'),
                behavior=QgsProcessingParameterFile.Folder,
                optional=True
            )
        )
        
        self.addParameter(
            QgsProcessingParameterString(
                self.FILE_PATTERN,
                self.tr('File pattern (e.g., *.tif)'),
                defaultValue='*.tif',
                optional=True
            )
        )
        
        self.addParameter(
            QgsProcessingParameterNumber(
                self.NODATA_VALUE,
                self.tr('NoData value'),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=-9999.0
            )
        )
        
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.APPLY_THRESHOLD,
                self.tr('Apply noise threshold filter'),
                defaultValue=True
            )
        )
        
        self.addParameter(
            QgsProcessingParameterNumber(
                self.THRESHOLD_VALUE,
                self.tr('Threshold value (m)'),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.7,
                minValue=0.0,
                maxValue=100.0
            )
        )
        
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.OUTPUT,
                self.tr('DoD Output')
            )
        )
    
    def processAlgorithm(self, parameters, context, feedback):
        
        multi_feedback = QgsProcessingMultiStepFeedback(11, feedback)
        
        input_mode = self.parameterAsEnum(parameters, self.INPUT_MODE, context)
        nodata = self.parameterAsDouble(parameters, self.NODATA_VALUE, context)
        apply_threshold = self.parameterAsBool(parameters, self.APPLY_THRESHOLD, context)
        threshold = self.parameterAsDouble(parameters, self.THRESHOLD_VALUE, context)
        output_path = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        
        feedback.pushInfo('=' * 60)
        feedback.pushInfo('DoDetector - DEM of Difference Analysis')
        feedback.pushInfo('Laboratory for Urban Boundaries (LUB)')
        feedback.pushInfo('=' * 60)
        
        # =====================================================================
        # STEP 1: Load inputs
        # =====================================================================
        multi_feedback.setCurrentStep(0)
        feedback.pushInfo('\n[Step 1/11] Loading inputs...')
        
        if input_mode == 0:
            feedback.pushInfo('  Mode: Single raster files')
            old_path, new_path, old_layer, new_layer = self._load_single_files(
                parameters, context, feedback
            )
        else:
            feedback.pushInfo('  Mode: Folders with DTM tiles')
            old_files, new_files = self._load_folders(parameters, context, feedback)
            old_path = old_files
            new_path = new_files
            old_layer = None
            new_layer = None
        
        if feedback.isCanceled():
            return {}
        
        # =====================================================================
        # STEP 2: Build VRT if multiple tiles
        # =====================================================================
        multi_feedback.setCurrentStep(1)
        feedback.pushInfo('\n[Step 2/11] Building Virtual Rasters (if needed)...')
        
        if input_mode == 1:
            old_path = self._build_vrt(old_path, 'old', context, feedback)
            new_path = self._build_vrt(new_path, 'new', context, feedback)
            old_layer = QgsRasterLayer(old_path, 'old_dtm')
            new_layer = QgsRasterLayer(new_path, 'new_dtm')
            feedback.pushInfo('  VRT layers created')
        else:
            feedback.pushInfo('  Single files - no VRT needed')
        
        if feedback.isCanceled():
            return {}
        
        # =====================================================================
        # STEP 3: Check CRS and reproject OLD to match NEW
        # =====================================================================
        multi_feedback.setCurrentStep(2)
        feedback.pushInfo('\n[Step 3/11] Checking and harmonizing CRS...')
        
        old_crs = old_layer.crs()
        new_crs = new_layer.crs()
        target_crs = new_crs  # Always use NEW DTM CRS as target
        
        feedback.pushInfo(f'  Old DTM CRS: {old_crs.authid()}')
        feedback.pushInfo(f'  New DTM CRS: {new_crs.authid()}')
        feedback.pushInfo(f'  Target CRS: {target_crs.authid()}')
        
        if old_crs != new_crs:
            feedback.pushInfo(f'  Reprojecting OLD DTM to {target_crs.authid()}...')
            old_path = self._reproject_raster(old_path, target_crs, nodata, context, feedback)
            old_layer = QgsRasterLayer(old_path, 'old_dtm_reprojected')
            feedback.pushInfo('  Reprojection complete')
        else:
            feedback.pushInfo('  CRS match - no reprojection needed')
        
        if feedback.isCanceled():
            return {}
        
        # =====================================================================
        # STEP 4: Calculate intersection extent
        # =====================================================================
        multi_feedback.setCurrentStep(3)
        feedback.pushInfo('\n[Step 4/11] Calculating intersection extent...')
        
        old_extent = old_layer.extent()
        new_extent = new_layer.extent()
        
        feedback.pushInfo(f'  Old extent: {old_extent.xMinimum():.0f}, {old_extent.yMinimum():.0f} to {old_extent.xMaximum():.0f}, {old_extent.yMaximum():.0f}')
        feedback.pushInfo(f'  New extent: {new_extent.xMinimum():.0f}, {new_extent.yMinimum():.0f} to {new_extent.xMaximum():.0f}, {new_extent.yMaximum():.0f}')
        
        # Calculate intersection
        common_extent = old_extent.intersect(new_extent)
        
        if common_extent.isEmpty():
            raise QgsProcessingException(
                'No overlapping area between the two DTMs. '
                'Check that both cover the same region.'
            )
        
        feedback.pushInfo(f'  Common extent: {common_extent.xMinimum():.0f}, {common_extent.yMinimum():.0f} to {common_extent.xMaximum():.0f}, {common_extent.yMaximum():.0f}')
        feedback.pushInfo(f'  Common area: {common_extent.width():.0f} x {common_extent.height():.0f} m')
        
        if feedback.isCanceled():
            return {}
        
        # =====================================================================
        # STEP 5: Clip and align BOTH rasters to common extent
        # =====================================================================
        multi_feedback.setCurrentStep(4)
        feedback.pushInfo('\n[Step 5/11] Clipping and aligning rasters...')
        
        # Use finer resolution
        old_res = old_layer.rasterUnitsPerPixelX()
        new_res = new_layer.rasterUnitsPerPixelX()
        target_res = min(old_res, new_res)
        
        feedback.pushInfo(f'  Old resolution: {old_res:.2f} m')
        feedback.pushInfo(f'  New resolution: {new_res:.2f} m')
        feedback.pushInfo(f'  Target resolution: {target_res:.2f} m')
        
        # Extent string for GDAL
        extent_str = f'{common_extent.xMinimum()},{common_extent.xMaximum()},{common_extent.yMinimum()},{common_extent.yMaximum()} [{target_crs.authid()}]'
        
        feedback.pushInfo('  Clipping OLD DTM...')
        old_clipped = self._clip_and_align(old_path, extent_str, target_res, target_crs, nodata, context, feedback)
        
        feedback.pushInfo('  Clipping NEW DTM...')
        new_clipped = self._clip_and_align(new_path, extent_str, target_res, target_crs, nodata, context, feedback)
        
        # Verify dimensions match
        old_clipped_layer = QgsRasterLayer(old_clipped, 'old_clipped')
        new_clipped_layer = QgsRasterLayer(new_clipped, 'new_clipped')
        
        feedback.pushInfo(f'  Old clipped: {old_clipped_layer.width()} x {old_clipped_layer.height()} pixels')
        feedback.pushInfo(f'  New clipped: {new_clipped_layer.width()} x {new_clipped_layer.height()} pixels')
        
        if feedback.isCanceled():
            return {}
        
        # =====================================================================
        # STEP 6: Convert 0 values to NoData
        # =====================================================================
        multi_feedback.setCurrentStep(5)
        feedback.pushInfo('\n[Step 6/11] Converting 0 values to NoData...')
        
        old_fixed = self._convert_zero_to_nodata(old_clipped, nodata, context, feedback)
        new_fixed = self._convert_zero_to_nodata(new_clipped, nodata, context, feedback)
        
        feedback.pushInfo('  Zero values converted to NoData')
        
        if feedback.isCanceled():
            return {}
        
        # =====================================================================
        # STEP 7: Create valid data mask (where BOTH have data)
        # =====================================================================
        multi_feedback.setCurrentStep(6)
        feedback.pushInfo('\n[Step 7/11] Creating common valid data mask...')
        
        # Create mask where BOTH rasters have valid data (not 0, not nodata)
        common_mask = self._create_common_mask(old_fixed, new_fixed, nodata, context, feedback)
        
        feedback.pushInfo('  Common valid mask created')
        
        if feedback.isCanceled():
            return {}
        
        # =====================================================================
        # STEP 8: Apply mask to both rasters
        # =====================================================================
        multi_feedback.setCurrentStep(7)
        feedback.pushInfo('\n[Step 8/11] Applying mask to rasters...')
        
        old_masked = self._apply_mask(old_fixed, common_mask, nodata, context, feedback)
        new_masked = self._apply_mask(new_fixed, common_mask, nodata, context, feedback)
        
        feedback.pushInfo('  Mask applied - only common valid areas remain')
        
        if feedback.isCanceled():
            return {}
        
        # =====================================================================
        # STEP 9: Calculate difference (New - Old)
        # =====================================================================
        multi_feedback.setCurrentStep(8)
        feedback.pushInfo('\n[Step 9/11] Calculating difference (New - Old)...')
        
        if apply_threshold:
            dod_path = self._calculate_dod_with_threshold(
                old_masked, new_masked, threshold, nodata, output_path, context, feedback
            )
            feedback.pushInfo(f'  Threshold: +/-{threshold} m')
        else:
            dod_path = self._calculate_dod(
                old_masked, new_masked, nodata, output_path, context, feedback
            )
        
        feedback.pushInfo('  DoD calculation complete')
        
        if feedback.isCanceled():
            return {}
        
        # =====================================================================
        # STEP 10: Report statistics
        # =====================================================================
        multi_feedback.setCurrentStep(9)
        feedback.pushInfo('\n[Step 10/11] Computing statistics...')
        
        self._report_statistics(dod_path, feedback)
        
        if feedback.isCanceled():
            return {}
        
        # =====================================================================
        # STEP 11: Calculate TRI
        # =====================================================================
        multi_feedback.setCurrentStep(10)
        feedback.pushInfo('\n[Step 11/11] Calculating TRI...')
        
        tri_path = self._calculate_tri(dod_path, context, feedback)
        
        feedback.pushInfo('\n' + '=' * 60)
        feedback.pushInfo('DoD calculation complete!')
        feedback.pushInfo('=' * 60)
        
        return {self.OUTPUT: dod_path}
    
    # =========================================================================
    # HELPER METHODS
    # =========================================================================
    
    def _load_single_files(self, parameters, context, feedback):
        old_layer = self.parameterAsRasterLayer(parameters, self.DTM_OLD_FILE, context)
        new_layer = self.parameterAsRasterLayer(parameters, self.DTM_NEW_FILE, context)
        
        if not old_layer or not old_layer.isValid():
            raise QgsProcessingException('Invalid Old/Reference DTM')
        if not new_layer or not new_layer.isValid():
            raise QgsProcessingException('Invalid New/Comparison DTM')
        
        feedback.pushInfo(f'  Old DTM: {os.path.basename(old_layer.source())}')
        feedback.pushInfo(f'  New DTM: {os.path.basename(new_layer.source())}')
        
        return old_layer.source(), new_layer.source(), old_layer, new_layer
    
    def _load_folders(self, parameters, context, feedback):
        old_folder = self.parameterAsFile(parameters, self.DTM_OLD_FOLDER, context)
        new_folder = self.parameterAsFile(parameters, self.DTM_NEW_FOLDER, context)
        file_pattern = self.parameterAsString(parameters, self.FILE_PATTERN, context)
        
        if not old_folder or not os.path.isdir(old_folder):
            raise QgsProcessingException('Invalid Old DTM folder')
        if not new_folder or not os.path.isdir(new_folder):
            raise QgsProcessingException('Invalid New DTM folder')
        
        old_files = glob.glob(os.path.join(old_folder, '**', file_pattern), recursive=True)
        new_files = glob.glob(os.path.join(new_folder, '**', file_pattern), recursive=True)
        
        if not old_files:
            raise QgsProcessingException(f'No files matching "{file_pattern}" in old folder')
        if not new_files:
            raise QgsProcessingException(f'No files matching "{file_pattern}" in new folder')
        
        feedback.pushInfo(f'  Old tiles: {len(old_files)}')
        feedback.pushInfo(f'  New tiles: {len(new_files)}')
        
        return old_files, new_files
    
    def _build_vrt(self, input_files, name, context, feedback):
        if isinstance(input_files, str):
            return input_files
        
        result = processing.run(
            'gdal:buildvirtualraster',
            {
                'INPUT': input_files,
                'RESOLUTION': 0,
                'SEPARATE': False,
                'PROJ_DIFFERENCE': False,
                'ADD_ALPHA': False,
                'ASSIGN_CRS': None,
                'RESAMPLING': 0,
                'OUTPUT': 'TEMPORARY_OUTPUT'
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True
        )
        return result['OUTPUT']
    
    def _reproject_raster(self, input_path, target_crs, nodata, context, feedback):
        result = processing.run(
            'gdal:warpreproject',
            {
                'INPUT': input_path,
                'SOURCE_CRS': None,
                'TARGET_CRS': target_crs,
                'RESAMPLING': 1,  # Bilinear
                'NODATA': nodata,
                'TARGET_RESOLUTION': None,
                'OPTIONS': 'COMPRESS=LZW|TILED=YES',
                'DATA_TYPE': 0,
                'MULTITHREADING': True,
                'OUTPUT': 'TEMPORARY_OUTPUT'
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True
        )
        return result['OUTPUT']
    
    def _clip_and_align(self, input_path, extent_str, resolution, crs, nodata, context, feedback):
        """Clip raster to extent and resample to target resolution."""
        result = processing.run(
            'gdal:warpreproject',
            {
                'INPUT': input_path,
                'SOURCE_CRS': None,
                'TARGET_CRS': crs,
                'RESAMPLING': 1,  # Bilinear
                'NODATA': nodata,
                'TARGET_RESOLUTION': resolution,
                'TARGET_EXTENT': extent_str,
                'TARGET_EXTENT_CRS': crs,
                'OPTIONS': 'COMPRESS=LZW|TILED=YES',
                'DATA_TYPE': 0,
                'MULTITHREADING': True,
                'OUTPUT': 'TEMPORARY_OUTPUT'
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True
        )
        return result['OUTPUT']
    
    def _convert_zero_to_nodata(self, input_path, nodata, context, feedback):
        """Convert 0 values to NoData."""
        result = processing.run(
            'gdal:rastercalculator',
            {
                'INPUT_A': input_path,
                'BAND_A': 1,
                'FORMULA': f'numpy.where(A == 0, {nodata}, A)',
                'NO_DATA': nodata,
                'RTYPE': 5,  # Float32
                'OPTIONS': 'COMPRESS=LZW|TILED=YES',
                'OUTPUT': 'TEMPORARY_OUTPUT'
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True
        )
        return result['OUTPUT']
    
    def _create_common_mask(self, old_path, new_path, nodata, context, feedback):
        """Create mask where BOTH rasters have valid data."""
        # Valid = not 0 AND not nodata
        # Mask = 1 where both valid, 0 otherwise
        result = processing.run(
            'gdal:rastercalculator',
            {
                'INPUT_A': old_path,
                'BAND_A': 1,
                'INPUT_B': new_path,
                'BAND_B': 1,
                'FORMULA': f'((A != {nodata}) * (A != 0) * (B != {nodata}) * (B != 0)) * 1.0',
                'NO_DATA': 0,
                'RTYPE': 5,  # Float32
                'OPTIONS': 'COMPRESS=LZW|TILED=YES',
                'OUTPUT': 'TEMPORARY_OUTPUT'
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True
        )
        return result['OUTPUT']
    
    def _apply_mask(self, input_path, mask_path, nodata, context, feedback):
        """Apply mask - set invalid areas to NoData."""
        result = processing.run(
            'gdal:rastercalculator',
            {
                'INPUT_A': input_path,
                'BAND_A': 1,
                'INPUT_B': mask_path,
                'BAND_B': 1,
                'FORMULA': f'numpy.where(B == 1, A, {nodata})',
                'NO_DATA': nodata,
                'RTYPE': 5,  # Float32
                'OPTIONS': 'COMPRESS=LZW|TILED=YES',
                'OUTPUT': 'TEMPORARY_OUTPUT'
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True
        )
        return result['OUTPUT']
    
    def _calculate_dod(self, old_path, new_path, nodata, output_path, context, feedback):
        result = processing.run(
            'gdal:rastercalculator',
            {
                'INPUT_A': new_path,
                'BAND_A': 1,
                'INPUT_B': old_path,
                'BAND_B': 1,
                'FORMULA': 'A - B',
                'NO_DATA': nodata,
                'RTYPE': 5,  # Float32
                'OPTIONS': 'COMPRESS=LZW|TILED=YES|BIGTIFF=YES',
                'OUTPUT': output_path
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True
        )
        return result['OUTPUT']
    
    def _calculate_dod_with_threshold(self, old_path, new_path, threshold, nodata, output_path, context, feedback):
        # DoD with threshold: keep values where |change| >= threshold
        formula = f'numpy.where(numpy.abs(A - B) >= {threshold}, A - B, 0)'
        
        result = processing.run(
            'gdal:rastercalculator',
            {
                'INPUT_A': new_path,
                'BAND_A': 1,
                'INPUT_B': old_path,
                'BAND_B': 1,
                'FORMULA': formula,
                'NO_DATA': nodata,
                'RTYPE': 5,  # Float32
                'OPTIONS': 'COMPRESS=LZW|TILED=YES|BIGTIFF=YES',
                'OUTPUT': output_path
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True
        )
        return result['OUTPUT']
    
    def _report_statistics(self, raster_path, feedback):
        try:
            layer = QgsRasterLayer(raster_path, 'DoD')
            if not layer.isValid():
                return
            
            provider = layer.dataProvider()
            stats = provider.bandStatistics(1)
            
            feedback.pushInfo('')
            feedback.pushInfo('-' * 45)
            feedback.pushInfo('           DoD STATISTICS')
            feedback.pushInfo('-' * 45)
            feedback.pushInfo(f'  Min:      {stats.minimumValue:>10.3f} m')
            feedback.pushInfo(f'  Max:      {stats.maximumValue:>10.3f} m')
            feedback.pushInfo(f'  Mean:     {stats.mean:>10.3f} m')
            feedback.pushInfo(f'  StdDev:   {stats.stdDev:>10.3f} m')
            feedback.pushInfo('-' * 45)
            
            if stats.mean > 0.1:
                feedback.pushInfo('  Net: GAIN (deposition/fill)')
            elif stats.mean < -0.1:
                feedback.pushInfo('  Net: LOSS (erosion/cut)')
            else:
                feedback.pushInfo('  Net: Balanced')
            feedback.pushInfo('-' * 45)
            
        except Exception as e:
            feedback.pushInfo(f'  Statistics error: {str(e)}')
    
    def _calculate_tri(self, dod_path, context, feedback):
        try:
            dod_dir = os.path.dirname(dod_path)
            dod_name = os.path.splitext(os.path.basename(dod_path))[0]
            tri_path = os.path.join(dod_dir, f'{dod_name}_TRI.tif')
            
            result = processing.run(
                'gdal:triterrainruggednessindex',
                {
                    'INPUT': dod_path,
                    'BAND': 1,
                    'COMPUTE_EDGES': True,
                    'OPTIONS': 'COMPRESS=LZW|TILED=YES',
                    'OUTPUT': tri_path
                },
                context=context,
                feedback=feedback,
                is_child_algorithm=True
            )
            
            feedback.pushInfo(f'  TRI saved: {os.path.basename(tri_path)}')
            return result['OUTPUT']
            
        except Exception as e:
            feedback.pushInfo(f'  TRI error: {str(e)}')
            return None