# Nested export pivot assemblies

Blender의 피봇 부모 관계를 유지하면서 각 피봇을 별도 Static Mesh로 내보내고, Unreal에서는 `bc_<최상위 피봇 이름>` Blueprint로 함께 사용한다.

```text
Export
└─ window_wood_single_02                 Empty
   ├─ frame_piece                       Mesh
   └─ window_wood_single_02_glass        Empty
      └─ glass_piece                    Mesh
```

두 Empty와 각 피봇이 직접 소유하는 Mesh를 모두 `Export` 컬렉션에 직접 연결한다. Outliner에서 부모 아래에 보이는 것만으로는 컬렉션 연결을 대신하지 않는다. Send to Unreal의 **Combine assets → Child meshes**를 사용한다.

## 적용 조건

- 부모와 자식이 직접 연결된 일반 Empty이고, 각각 `Export`에 직접 연결되어 있어야 한다.
- 각 Empty에는 `Export`에 직접 연결된 일반 Static Mesh 자식이 하나 이상 있어야 한다.
- 컬렉션 인스턴스, Armature가 연결된 Mesh, 활성 Shape Key가 있는 Mesh, `SOCKET_` 및 `UBX_`/`UCP_`/`USP_`/`UCX_` 보조 오브젝트는 피봇 판정에 사용하지 않는다.
- 최상위 피봇 위쪽의 어느 부모라도 Armature이면 조립 규칙을 적용하지 않는다.
- 여러 단계로 중첩할 수 있다. 중간에 조건을 만족하지 않는 부모가 있으면 별도의 피봇 연결로 취급하지 않는다.

일반 단일 피봇, 기존 GPro 컬렉션 인스턴스, 리깅 계층, `Export` 하위 컬렉션에만 연결된 그룹은 기존 처리 방식을 유지한다. 이름에 `_glass`가 포함되어 있다는 이유만으로 조립이나 재질 분류를 시작하지 않는다.

## 출력과 재전송

예시에서는 `window_wood_single_02`와 `window_wood_single_02_glass` 두 Static Mesh 및 각각의 재질 JSON을 만든다. 자식 피봇의 메시와 재질은 부모 메시의 FBX/JSON에 합쳐지지 않는다. Handoff API와 검증 화면도 동일한 소유 피봇을 표시한다.

한 번의 전송에서 필요한 모든 피봇의 실제 Unreal import 완료 기록이 모이면, 최상위 메시와 같은 폴더에 `bc_window_wood_single_02`를 만든다. 최상위 메시가 Blueprint의 루트 컴포넌트가 되고, glass 메시 컴포넌트가 그 아래에 붙는다. 누락되거나 실패한 자식을 이전에 존재하던 메시로 대신하여 조립하지 않는다.

조립용 메시에는 import 작업 결과에 정확한 대상 패키지가 포함되고 실제 `StaticMesh`로 로드되는지 확인한다. 결과가 비었거나 다른 에셋이면 재질 처리와 완료 기록 전에 실패한다. 이 추가 검사는 조립 대상으로 표시된 Static Mesh에만 적용한다.

FBX에는 피봇의 월드 위치를 뺀 메시가 저장된다. 회전과 스케일은 이미 지오메트리에 반영되어 있으므로 Blueprint에서 다시 적용하지 않고, 부모 피봇과의 위치 차이만 Unreal 좌표와 센티미터 단위로 보존한다. 루트 컴포넌트의 변환은 identity이며 배치는 Blueprint Actor로 조절한다.

재전송은 같은 이름의 Blueprint를 갱신한다. `Send2UE.NestedPivots.Owner` 메타데이터가 같은 루트 메시를 가리키는 경우에만 갱신하며, 같은 이름의 사용자 Blueprint를 덮어쓰지 않는다. 생성된 컴포넌트만 관리하고 사용자 컴포넌트는 유지한다. 이름 충돌, 사용자가 교체한 루트, 삭제 대상 피봇 아래의 사용자 컴포넌트가 있으면 변경을 거부한다.

## Glass와 Nanite

피봇 분리와 반투명 판정은 별개다. 현재 창문의 `glass` 재질은 Blender의 `surface_render_method`가 `BLENDED`여야 기존 재질 handoff에서 `translucent: true`로 기록된다. 이번 설정은 해당 라이브 재질에만 적용하며, 다른 재질의 전역 판정 규칙을 변경하지 않는다.

기존 Unreal 재질 파이프라인은 반투명으로 표시된 glass 메시의 Nanite를 끄고 공용 `/Game/Material/AssetSurface/MI/MI_Prop_Glass_01`을 할당한다. 불투명 창틀 메시의 Nanite 설정은 기존 불투명 처리 규칙을 따른다. Blueprint 생성기는 메시의 Nanite나 공용 재질을 별도로 수정하지 않는다.

## 검증

구조 판정 및 Blender 측 JSON 회귀 테스트는 일반 단일 피봇, 하위 컬렉션, 컬렉션 인스턴스, Armature 계층의 기존 출력과 비교한다. 실제 전송 검증에서는 두 FBX의 지오메트리/재질 분리, Unreal의 두 Static Mesh, glass의 Nanite 및 공용 MI, Blueprint의 루트/자식 메시 참조, 같은 Blueprint의 반복 갱신을 확인한다.
