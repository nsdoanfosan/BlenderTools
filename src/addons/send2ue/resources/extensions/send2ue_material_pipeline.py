# send2ue extension: import 직후 머티리얼 파이프라인 자동 실행
# =============================================================
# send2ue 가 FBX 를 언리얼로 import 한 "직후" post_import hook 이 호출된다.
# 이 hook 에서 언리얼에 RPC 로 ue_material_setup.process_mesh() 를 실행시킨다.
#
# 이 방식의 장점:
#   - on_asset_post_import 델리게이트와 달리 send2ue 가 직접 호출하므로
#     Interchange/Legacy 어느 import 경로든 100% 트리거된다.
#   - 이 시점엔 메쉬+텍스처가 모두 import 완료된 상태 → 타이밍 문제 없음.
#
# 설치 위치(둘 중 하나):
#   A) send2ue/resources/extensions/  (자동 로드, 단 send2ue 업데이트 시 재복사 필요)
#   B) send2ue Preferences 의 Extension Folder 로 이 파일이 있는 폴더를 등록
#
# 실제 처리 로직은 언리얼 측 ue_material_setup.py 가 담당한다(수정은 거기서).

import os
import bpy
from send2ue.core.extension import ExtensionBase
from send2ue.constants import UnrealTypes
from send2ue.dependencies.unreal import run_commands

# 언리얼에서 ue_material_setup.py 를 찾을 경로.
# UE_BLENDER_PIPELINE_DIR 환경변수로 덮어쓸 수 있고, 없으면 ~/Documents/UE_Blender_Pipeline 사용.
PIPELINE_DIR = os.environ.get(
    'UE_BLENDER_PIPELINE_DIR',
    os.path.join(os.path.expanduser('~'), 'Documents', 'UE_Blender_Pipeline')
).replace('\\', '/')


class MaterialPipelineExtension(ExtensionBase):
    name = 'material_pipeline'

    enabled: bpy.props.BoolProperty(
        name="Auto-setup materials on import",
        default=True,
        description=(
            "Import 후 이름이 매칭되는(M_뗀 머티리얼명이 메쉬명으로 시작) StaticMesh 에 대해 "
            "텍스처를 /Game/Textures 로 옮기고 M_Prop_Master 인스턴스를 생성/연결/할당한다."
        ),
    )

    def post_import(self, asset_data, properties):
        """각 에셋 import 직후 호출. StaticMesh 면 언리얼에서 후처리 실행."""
        if not self.enabled:
            return
        if asset_data.get('skip'):
            return
        if asset_data.get('_asset_type') != UnrealTypes.STATIC_MESH:
            return

        asset_path = asset_data.get('asset_path')
        if not asset_path:
            return

        # 언리얼 에디터에서 실행될 파이썬 명령들
        commands = [
            'import sys',
            f'_d = r"{PIPELINE_DIR}"',
            'sys.path.append(_d) if _d not in sys.path else None',
            'import importlib',
            'import ue_material_setup as _p',
            'importlib.reload(_p)',
            f'_p.process_mesh(r"{asset_path}")',
        ]
        run_commands(commands)

    def draw_import(self, dialog, layout, properties):
        box = layout.box()
        box.label(text='Material Pipeline (M_Prop_Master 자동 연결):')
        dialog.draw_property(self, box, 'enabled')
