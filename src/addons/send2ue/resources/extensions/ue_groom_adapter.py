# Copyright Epic Games, Inc. All Rights Reserved.

import bpy

from send2ue.core import ue_groom_adapter, utilities
from send2ue.core.extension import ExtensionBase


class ValidateUeGroomData(bpy.types.Operator):
    bl_idname = 'send2ue.validate_ue_groom_data'
    bl_label = 'Validate Hair Tool Groom Data'
    bl_description = 'Validate evaluated Hair Tool curve IDs, Root UVs, guides, parents, and widths'

    def execute(self, context):
        if not ue_groom_adapter.is_enabled(context.scene.send2ue):
            self.report({'INFO'}, 'UE Groom Adapter is disabled')
            return {'CANCELLED'}
        validation = ue_groom_adapter.validate_scene(context.scene.send2ue)
        for warning in validation['warnings']:
            print(f'[UE Groom Adapter] WARNING: {warning}')
        if validation['errors']:
            self.report({'ERROR'}, validation['errors'][0])
            for error in validation['errors'][1:]:
                print(f'[UE Groom Adapter] ERROR: {error}')
            return {'CANCELLED'}

        curve_count = sum(report.get('curve_count', 0) for report in validation['objects'])
        self.report(
            {'INFO'},
            f'UE Groom validation passed: {len(validation["objects"])} object(s), {curve_count} curves',
        )
        return {'FINISHED'}


class ConfigureCyclesGroomView(bpy.types.Operator):
    bl_idname = 'send2ue.configure_cycles_groom_view'
    bl_label = 'Apply Cycles Groom View'
    bl_description = (
        'Configure only the Cycles viewport preview; does not create mesh geometry '
        'or change final render samples'
    )

    def execute(self, context):
        if not ue_groom_adapter.is_enabled(context.scene.send2ue):
            self.report({'INFO'}, 'UE Groom Adapter is disabled')
            return {'CANCELLED'}
        adapter = ue_groom_adapter.get_settings(context.scene.send2ue)
        result = ue_groom_adapter.configure_cycles_groom_view(
            context,
            getattr(adapter, 'cycles_view_preset', ue_groom_adapter.CYCLES_VIEW_FAST),
        )
        self.report(
            {'INFO'},
            f'Cycles Groom View: {result["preset"]}, {result["preview_samples"]} preview samples',
        )
        return {'FINISHED'}


class ConnectHairData(bpy.types.Operator):
    bl_idname = 'send2ue.connect_hair_data'
    bl_label = 'Connect Hair Tool Data'
    bl_description = (
        'Connect the external Groom preview to Hair Tool HairShaderMain values, '
        'Factor/SystemColor attributes, evaluated radius, and GUIDE rules without editing Hair Tool'
    )

    def execute(self, context):
        properties = context.scene.send2ue
        if not ue_groom_adapter.is_enabled(properties):
            self.report({'INFO'}, 'UE Groom Adapter is disabled')
            return {'CANCELLED'}
        objects = ue_groom_adapter.get_hair_tool_grooms(properties)
        active = context.view_layer.objects.active
        if active in objects:
            objects = [active]
        if not objects:
            self.report({'ERROR'}, 'No Hair Tool Groom Curves found in the Export collection')
            return {'CANCELLED'}
        try:
            for scene_object in objects:
                ue_groom_adapter.connect_hair_tool_guide_rules(scene_object)
                ue_groom_adapter.connect_hair_tool_preview_material(scene_object, properties)
                ue_groom_adapter.connect_hair_tool_radius_preview(scene_object, properties)
        except RuntimeError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f'Connected Hair Tool data on {len(objects)} Groom object(s)')
        return {'FINISHED'}


class UeGroomAdapterExtension(ExtensionBase):
    name = ue_groom_adapter.EXTENSION_NAME
    utility_operators = [
        ValidateUeGroomData,
        ConfigureCyclesGroomView,
        ConnectHairData,
    ]

    enabled: bpy.props.BoolProperty(
        name='Enable UE Groom Adapter',
        default=False,
        description=(
            'Opt in to the non-destructive Hair Tool Groom adapter; disabled keeps '
            'the standard Send to Unreal workflow unchanged'
        ),
    )

    output_mode: bpy.props.EnumProperty(
        name='Hair Tool Output',
        default=ue_groom_adapter.OUTPUT_CARDS,
        items=(
            (ue_groom_adapter.OUTPUT_CARDS, 'Cards', 'Export evaluated Hair Tool card meshes'),
            (ue_groom_adapter.OUTPUT_GROOM, 'Groom', 'Export evaluated Hair Tool curves as an Unreal Groom'),
            (ue_groom_adapter.OUTPUT_BOTH, 'Cards + Groom', 'Export both card mesh and Groom assets'),
        ),
    )

    deformation_preset: bpy.props.EnumProperty(
        name='Deformation Preset',
        default=ue_groom_adapter.PRESET_UE_RIGGED_GUIDES,
        items=(
            (
                ue_groom_adapter.PRESET_CARD_RIG,
                'Card Rig (Bone)',
                'Use Hair Tool bone/weight data for Cards; a Groom exported with it uses UE Generated Guides',
            ),
            (
                ue_groom_adapter.PRESET_UE_RIGGED_GUIDES,
                'UE Rigged Guides',
                'Let Unreal 5.8 generate rigged guides from Groom strands; does not create Hair Tool card bones',
            ),
        ),
    )

    rigged_guide_num_curves: bpy.props.IntProperty(
        name='Rigged Guide Curves',
        default=64,
        min=1,
        max=1024,
        description='Number of rigged guide curves generated per Groom group in Unreal',
    )

    rigged_guide_num_points: bpy.props.IntProperty(
        name='Rigged Guide Points',
        default=8,
        min=2,
        max=64,
        description='Number of points/bones generated on each rigged guide in Unreal',
    )

    default_width: bpy.props.FloatProperty(
        name='Default Width',
        default=0.001,
        min=0.000001,
        soft_max=0.02,
        precision=6,
        subtype='DISTANCE',
        description='Fallback strand diameter in Blender units when no width or radius exists',
    )

    id_attribute: bpy.props.StringProperty(
        name='ID',
        description='Blank = auto-map groom_id, then id',
    )
    group_id_attribute: bpy.props.StringProperty(
        name='Group ID',
        description='Blank = auto-map groom_group_id, then group_id',
    )
    root_uv_attribute: bpy.props.StringProperty(
        name='Root UV',
        description='Blank = prefer a FLOAT2 UVMap/root UV source',
    )
    guide_attribute: bpy.props.StringProperty(
        name='Guide',
        description='Blank = auto-map groom_guide or GUIDE',
    )
    parent_id_attribute: bpy.props.StringProperty(
        name='Parent ID',
        description='Blank = auto-map groom_parent_id or ParentID',
    )
    width_attribute: bpy.props.StringProperty(
        name='Width / Radius',
        description='Blank = auto-map groom_width, width, or radius',
    )
    color_attribute: bpy.props.StringProperty(
        name='Color',
        description='Blank = auto-map groom_color or the UE Groom preview color',
    )
    roughness_attribute: bpy.props.StringProperty(
        name='Roughness',
        description='Blank = auto-map groom_roughness or the preview Hair BSDF roughness',
    )
    ao_attribute: bpy.props.StringProperty(
        name='Ambient Occlusion',
        description='Blank = auto-map groom_ao or Hair Tool AO',
    )
    clump_id_attribute: bpy.props.StringProperty(
        name='Clump ID',
        description='Blank = auto-map groom_clump_id or ClumpID',
    )
    factor_attribute: bpy.props.StringProperty(
        name='Hair Tool Factor',
        description='Blank = auto-map Hair Tool Factor for root/tip color rules',
    )
    random_attribute: bpy.props.StringProperty(
        name='Hair Tool Random',
        description='Blank = auto-map Hair Tool Random for authored color variation',
    )
    roundness_attribute: bpy.props.StringProperty(
        name='Profile Roundness',
        description='Blank = inspect Hair Tool roundness; retained for Cards and reported as Groom-only limitation',
    )
    unreal_material_path: bpy.props.StringProperty(
        name='Unreal Groom Material',
        default=ue_groom_adapter.DEFAULT_UNREAL_GROOM_MATERIAL,
        description='Material asset created or reused under /Game/Material and assigned to the Groom',
    )

    cycles_view_preset: bpy.props.EnumProperty(
        name='Cycles Groom View',
        default=ue_groom_adapter.CYCLES_VIEW_FAST,
        items=(
            (
                ue_groom_adapter.CYCLES_VIEW_FAST,
                'Fast',
                '1 viewport sample, no denoising, faster scrambling distance',
            ),
            (
                ue_groom_adapter.CYCLES_VIEW_BALANCED,
                'Balanced',
                '8 viewport samples with denoising',
            ),
            (
                ue_groom_adapter.CYCLES_VIEW_QUALITY,
                'Quality',
                '32 viewport samples with denoising',
            ),
        ),
    )

    def pre_validations(self, properties):
        if not self.enabled or not ue_groom_adapter.wants_groom(properties):
            return True

        validation = ue_groom_adapter.validate_scene(properties)
        for warning in validation['warnings']:
            print(f'[UE Groom Adapter] WARNING: {warning}')
        if validation['errors']:
            utilities.report_error(
                'UE Groom Adapter validation failed. ',
                '\n'.join(validation['errors']),
            )
            return False
        return True

    def draw_export(self, dialog, layout, properties):
        box = layout.box()
        box.label(text='Hair Tool -> UE Groom Adapter:')
        dialog.draw_property(self, box, 'enabled')
        if not self.enabled:
            return
        dialog.draw_property(self, box, 'output_mode')
        dialog.draw_property(self, box, 'default_width')

        mappings = box.box()
        mappings.label(text='Attribute Mapping (blank = auto):')
        for property_name in (
            'id_attribute',
            'group_id_attribute',
            'root_uv_attribute',
            'guide_attribute',
            'parent_id_attribute',
            'width_attribute',
            'color_attribute',
            'roughness_attribute',
            'ao_attribute',
            'clump_id_attribute',
            'factor_attribute',
            'random_attribute',
            'roundness_attribute',
        ):
            dialog.draw_property(self, mappings, property_name)

        preview = box.box()
        preview.label(text='Blender / Unreal preview parity:')
        dialog.draw_property(self, preview, 'unreal_material_path')
        preview.operator(ConnectHairData.bl_idname, icon='LINKED')
        dialog.draw_property(self, preview, 'cycles_view_preset')
        preview.operator(ConfigureCyclesGroomView.bl_idname, icon='RESTRICT_RENDER_OFF')

    def draw_import(self, dialog, layout, properties):
        if not self.enabled:
            return
        box = layout.box()
        box.label(text='Hair Tool deformation:')
        dialog.draw_property(self, box, 'deformation_preset')
        if self.deformation_preset == ue_groom_adapter.PRESET_UE_RIGGED_GUIDES:
            dialog.draw_property(self, box, 'rigged_guide_num_curves')
            dialog.draw_property(self, box, 'rigged_guide_num_points')

    def draw_validations(self, dialog, layout, properties):
        if not self.enabled:
            return
        box = layout.box()
        box.label(text='Hair Tool Groom data:')
        box.operator(ValidateUeGroomData.bl_idname, icon='CHECKMARK')
