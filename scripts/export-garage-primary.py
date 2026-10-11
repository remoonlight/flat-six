"""Export the saved visible Blender composition, then apply explicit part refinements.

Run with Blender --background --factory-startup --python this_file -- --source ...
Game-derived assets stay under .local/garage-models; source files are never saved.
"""
import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import bpy
from mathutils import Matrix


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('.local/garage-models/boxster-primary.glb'))
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    hashes = {str(args.source.resolve()): hashlib.sha256(args.source.read_bytes()).hexdigest()}
    bpy.ops.wm.open_mainfile(filepath=str(args.source.resolve()))
    bpy.context.view_layer.update()
    source_scene = bpy.context.scene.name
    source_layer = bpy.context.view_layer.name
    views = [a.spaces.active for a in bpy.context.screen.areas if a.type == 'VIEW_3D']
    viewport = views[0] if views else None
    # Use viewport visibility, including saved local view / collection hiding.
    # hide_render is independent of what the user currently sees in the file.
    primary = [o for o in bpy.context.scene.objects if o.type == 'MESH'
               and o.visible_get(view_layer=bpy.context.view_layer, viewport=viewport)]
    assert primary, 'Empty primary composition'
    depsgraph = bpy.context.evaluated_depsgraph_get()
    # Blender +Y front -> glTF +Z front; exporter also converts Z-up to Y-up.
    product_rotation = Matrix.Rotation(math.pi, 4, 'Z')
    export_scene = bpy.data.scenes.new('GaragePrimaryExport')
    records = []
    material_cache = {}

    def classify(obj):
        name = obj.name
        if name in {'718__GEO_Chassis_SUB5', '718__GEO_INT_PLASTIC_BLACK_SUB0_Part_005'}:
            return 'Cabin', 'liner'
        mats = ' '.join(s.material.name for s in obj.material_slots if s.material)
        painted_panel = bool(obj.material_slots) and all(s.material and 'Car_Paint' in s.material.name for s in obj.material_slots)
        body_part = (name.startswith('SM_') or str(obj.get('merge_role', '')).startswith('981车身')) and not name.startswith('SM_Interior')
        if painted_panel or body_part:
            zone = 'rear-trunk' if name.endswith('__tail_lid') else 'front-bumper' if name.startswith('SM_FrontKit') else 'rear-bumper' if name.startswith('SM_RearKit') else 'front-trunk' if name.startswith('SM_Hood') or 'GEO_Hood' in name else 'soft-top' if name.startswith('SM_SoftTop') else 'other'
            return 'Exterior', zone
        if name.endswith('__tail_lid'):
            return 'Exterior', 'rear-trunk'
        if name.startswith(('leftDoorTrim', 'rightDoorTrim')):
            return 'Cabin', 'door-trim'
        if name.startswith(('leftSeat', 'rightSeat')):
            return 'Cabin', 'seats'
        if name.startswith(('centerConsole', 'manualShifter')):
            return 'Cabin', 'console'
        if not name.startswith(('718__', 'SM_Interior')) and name not in {'尾排', 'Source__hq_body_186__part_020', 'Source__hq_body_186__part_021'}:
            return 'Cabin', 'liner' if name.startswith(('981__', 'rearCabin')) else 'dashboard'
        if 'DOOR_GLASS' in name:
            return 'Exterior', 'windows'
        if name.startswith('SM_Interior') or (any(m.startswith(('INT_', 'Cockpit_', 'Inner_')) for m in mats.split()) and 'GLASS' not in name):
            group = str(obj.get('component_group', ''))
            zone = 'door-trim' if '门板' in group else 'seats' if '座椅' in group else 'console' if any(s in group for s in ['中控', '换挡']) else 'dashboard' if any(s in group for s in ['仪表', '方向盘']) else 'liner'
            return 'Cabin', zone
        upper = name.upper()
        zone = 'front-bumper' if 'F_BUMPER' in upper or 'LIGHT_FRONT' in upper else 'rear-bumper' if 'R_BUMPER' in upper or 'LIGHT_REAR' in upper else 'front-trunk' if 'HOOD' in upper else 'rear-trunk' if 'WING' in upper else 'other'
        return 'Exterior', zone

    def add(obj, matrix, source, role, category=None, zone=None):
        category, zone = (category, zone) if category else classify(obj)
        evaluated = obj.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
        clone = bpy.data.objects.new('export', mesh)
        export_scene.collection.objects.link(clone)
        clone.matrix_world = matrix
        # Resolve object-level material overrides before exporting.
        clone.data.materials.clear()
        for slot in obj.material_slots:
            mat = slot.material
            if mat:
                if mat.name not in material_cache:
                    converted = mat.copy()
                    material_cache[mat.name] = converted
                    if 'Transparent_Glass' in mat.name:
                        # glTF has no Glass BSDF. Translate its saved values;
                        # existing Principled materials (including mirrors) stay intact.
                        glass_node = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_GLASS')
                        color = tuple(glass_node.inputs['Color'].default_value)
                        roughness = glass_node.inputs['Roughness'].default_value
                        ior = glass_node.inputs['IOR'].default_value
                        converted.node_tree.nodes.clear()
                        shader = converted.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
                        output = converted.node_tree.nodes.new('ShaderNodeOutputMaterial')
                        converted.node_tree.links.new(shader.outputs[0], output.inputs['Surface'])
                        shader.inputs['Base Color'].default_value = color
                        shader.inputs['Metallic'].default_value = 0
                        shader.inputs['Roughness'].default_value = roughness
                        shader.inputs['IOR'].default_value = ior
                        shader.inputs['Transmission Weight'].default_value = 1
                        shader.inputs['Alpha'].default_value = mat.diffuse_color[3]
                        converted.surface_render_method = 'DITHERED'
                mat = material_cache[mat.name]
            clone.data.materials.append(mat)
        paint = bool(obj.material_slots) and all(s.material and 'Car_Paint' in s.material.name for s in obj.material_slots)
        clone.name = f'{category}__{zone}__{obj.name}' + ('__Car_Paint' if paint else '')
        clone.data.name = clone.name
        clone['garageCategory'] = category.lower()
        clone['garageZone'] = zone
        clone['sourceObject'] = obj.name
        clone['sourceRole'] = role
        clone['sourceAsset'] = Path(source).name
        clone['preserveGarageMaterial'] = True
        records.append({'name': clone.name, 'original': obj.name, 'category': category, 'zone': zone, 'role': role, 'source': Path(source).name})
        return clone

    for obj in primary:
        add(obj, product_rotation @ obj.evaluated_get(depsgraph).matrix_world, args.source, 'primary')
    primary_count = len(records)
    export_scene['garageComposition'] = 'saved-visible'
    export_scene['sourceScene'] = source_scene
    export_scene['sourceViewLayer'] = source_layer
    bpy.context.window.scene = export_scene
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action='SELECT')
    temp = args.output.with_name(args.output.stem + '.pending.glb')
    bpy.ops.export_scene.gltf(filepath=str(temp.resolve()), export_format='GLB', use_selection=True,
                              export_extras=True, export_yup=True, export_animations=False,
                              export_cameras=False, export_lights=False)
    for file, expected in hashes.items():
        assert hashlib.sha256(Path(file).read_bytes()).hexdigest() == expected, 'Source changed during export'
    assert temp.stat().st_size > 10000
    temp.replace(args.output)
    report = {'sourceHashes': hashes, 'sourceScene': source_scene, 'sourceViewLayer': source_layer,
              'composition': 'saved-visible', 'visibleSourceObjects': [o.name for o in primary],
              'output': str(args.output.resolve()), 'primaryMeshes': primary_count,
              'supplementMeshes': 0, 'parts': records,
              'caveat': 'Local visual reference: mixed 718/982 and GT7/981 sources; not OEM fit verification.'}
    args.output.with_suffix('.manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    subprocess.run(['node', str(Path(__file__).with_name('refine-garage-primary.mjs')),
                    '--glb', str(args.output), '--apply'], check=True)
    report = json.loads(args.output.with_suffix('.manifest.json').read_text(encoding='utf8'))
    print('GARAGE_EXPORT_OK', json.dumps({k: v for k, v in report.items() if k not in ['parts', 'sourceHashes', 'visibleSourceObjects']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
