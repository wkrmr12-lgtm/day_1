"""
제주도 DEM + 물결치는 바다 + 하늘을 이동하는 태양  (Blender 5.x / 4.x)
====================================================================

실행 방법 (둘 중 하나)
  1) Blender 실행 → 상단 [Scripting] 탭 → [Open]으로 이 파일 열기 → ▶ (Run Script)
  2) 명령줄:  blender --python jeju_ocean_sun.py

만들어지는 것
  - 제주도 지형   : DEM_PATH의 DEM 이미지로 지형 메시 생성 (비워 두면 가상 제주도 지형)
  - 바다         : Ocean 모디파이어로 실제 물결 (카메라 앞 넓은 구역) + 수평선까지 이어지는 먼 바다
  - 하늘과 태양   : 다중 산란 하늘(Sky Texture) + 태양 램프, 오후 → 해 질 녘까지 태양이 이동
  - 카메라 2대    : Cam_Coast(해안 가까이에서 본 파도와 태양·기본), Cam_Island(북쪽 상공에서 본 섬과 남쪽 바다)

좌표 기준:  +X = 동쪽, +Y = 북쪽, +Z = 위,  Blender 1칸 = 1 m,  해수면 z = 0
"""

import math
import os

import bmesh
import bpy
import numpy as np
from mathutils import Euler, Vector

# =====================================================================
# 1. 설정 (여기만 바꾸면 됩니다)
# =====================================================================

# --- DEM -------------------------------------------------------------
# 비워 두면 가상 제주도 지형을 만듭니다.
# 실제 DEM을 쓰려면 경로를 넣으세요.  예) r"C:\data\jeju_dem.tif"
#   · 32비트 실수 GeoTIFF : 픽셀 값이 그대로 높이(m)로 쓰입니다.
#   · 16비트 PNG          : 0~1 값에 DEM_NORMALIZED_MAX_M을 곱해 높이(m)로 바꿉니다.
DEM_PATH = r""

# DEM 이미지가 덮는 실제 거리 (m).  아래 범위로 자른 DEM이면 기본값 그대로 쓰면 됩니다.
#   경도 126.14°E ~ 126.97°E,  위도 33.11°N ~ 33.57°N   (약 77 km × 51 km)
DEM_WIDTH_M = 77_000        # 동서
DEM_HEIGHT_M = 51_000       # 남북
DEM_NORMALIZED_MAX_M = 2000  # 정규화(0~1) 이미지에서 1.0이 뜻하는 높이
DEM_NODATA_BELOW_M = -100    # 이보다 낮은 값(NoData)은 바다로 처리

GRID_MAX = 1000              # 지형 격자 해상도 (긴 쪽 기준 점 개수). 컴퓨터가 느리면 600
VERTICAL_EXAGGERATION = 1.5  # 높이 과장 배율 (1.0 = 실제 높이)
SEA_DEPTH_M = 60             # 바닷속 지형 깊이

# --- 애니메이션 / 태양 -------------------------------------------------
FPS = 24
FRAME_END = 240              # 10초

# 태양 방위각: 북 = 0°, 동 = 90°, 남 = 180°, 서 = 270°
SUN_AZIMUTH_START = 175      # 오후 (남쪽)
SUN_AZIMUTH_END = 245        # 해 질 녘 (남서쪽)
SUN_ELEVATION_START = 22     # 시작 고도 (°)
SUN_ELEVATION_END = 1        # 끝 고도 (°) - 수평선 바로 위
SUN_LAMP_ENERGY = 2.0        # 태양 램프 세기 (하늘의 태양도 빛을 내므로 낮게)

# --- 카메라 -----------------------------------------------------------
CAMERA_COAST_X_M = -10_000   # 해안 카메라를 둘 남쪽 해안의 동서 위치 (섬 중심 기준, 서쪽이 -)
CAMERA_OFFSHORE_M = 120      # 해안선에서 바다 쪽으로 떨어진 거리
CAMERA_HEIGHT_M = 25         # 해수면 위 높이
CAMERA_AZIMUTH = 232         # 바라보는 방향 (남서쪽 = 해가 지는 쪽, 오른쪽에 서쪽 해안선)
CAMERA_PITCH = 3             # 위로 드는 각도 (°)
CAMERA_LENS_MM = 24

# --- 바다 (Ocean 모디파이어) -------------------------------------------
OCEAN_TILE_M = 200           # 파도 계산 타일 크기
OCEAN_REPEAT = 12            # 타일 반복 수 (가로·세로) → 2.4 km × 2.4 km 의 실제 물결 구역
OCEAN_RESOLUTION = 16        # 렌더 해상도 (높을수록 잔물결이 자세함, 느려짐)
OCEAN_VIEWPORT_RES = 5       # 화면 미리보기 해상도
OCEAN_WIND_MS = 9            # 풍속 (m/s) → 파도 크기
OCEAN_CHOPPINESS = 1.3       # 파도 끝의 뾰족함
OCEAN_TIME_SPEED = 1.0       # 파도 속도 배율

# --- 렌더 -------------------------------------------------------------
RENDER_ENGINE = "CYCLES"     # 바다에 비친 태양을 제대로 보려면 CYCLES 권장 ("BLENDER_EEVEE" 가능)
RENDER_SAMPLES = 64          # 노이즈 제거(denoise)를 켜 두어 64로도 충분
RESOLUTION = (1920, 1080)
RESOLUTION_PERCENT = 100     # 시험 렌더는 50으로 낮추면 약 4배 빨라짐
OUTPUT_NAME = "jeju_sunset_"

# =====================================================================
# 2. 도우미 함수
# =====================================================================

M_PER_BU = 1.0  # Blender 1칸 = 1 m (파도 물리 계산이 실제 크기와 맞도록)


def log(*args):
    print("[제주 바다]", *args)


def set_if(obj, attr, value):
    """속성이 있는 버전에서만 값을 넣는다."""
    if hasattr(obj, attr):
        try:
            setattr(obj, attr, value)
            return True
        except (TypeError, ValueError, AttributeError):
            pass
    return False


def set_input(node, names, value):
    """노드 입력 소켓 이름이 버전마다 달라도 동작하도록 여러 이름을 시도."""
    for n in names if isinstance(names, (list, tuple)) else [names]:
        sock = node.inputs.get(n)
        if sock is not None:
            sock.default_value = value
            return sock
    return None


def socket(sockets, name, stype=None):
    for s in sockets:
        if s.name == name and (stype is None or s.type == stype):
            return s
    raise KeyError(name)


def fresh_node_tree(idblock):
    """머티리얼/월드의 노드 트리를 비우고 돌려준다."""
    if getattr(idblock, "node_tree", None) is None:
        idblock.use_nodes = True
    nt = idblock.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    return nt


def link_object(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def clear_scene():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.lights,
                 bpy.data.cameras, bpy.data.images, bpy.data.worlds):
        for block in list(coll):
            if block.users == 0:
                coll.remove(block)


def sun_direction(az_deg, el_deg):
    """방위각/고도 → 태양 쪽을 가리키는 단위 벡터."""
    a, e = math.radians(az_deg), math.radians(el_deg)
    return Vector((math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e)))


# =====================================================================
# 3. 지형 높이 데이터 (DEM 또는 가상 제주도)
# =====================================================================

def synthetic_jeju(cols, rows):
    """DEM이 없을 때 쓰는 가상 제주도: 타원형 순상 화산 + 한라산 + 오름 + 성산일출봉."""
    xs = (np.arange(cols) / (cols - 1) - 0.5) * DEM_WIDTH_M
    ys = (np.arange(rows) / (rows - 1) - 0.5) * DEM_HEIGHT_M
    X, Y = np.meshgrid(xs, ys)

    th = math.radians(14)  # 섬의 긴 축이 동북동 방향으로 기울어짐
    xr = X * math.cos(th) + Y * math.sin(th)
    yr = -X * math.sin(th) + Y * math.cos(th)

    a, b = 35_500.0, 15_000.0
    ang = np.arctan2(yr / b, xr / a)
    wobble = 1 + 0.035 * np.sin(ang * 7 + 0.4) + 0.025 * np.sin(ang * 13 + 1.7) + 0.015 * np.sin(ang * 23)
    r = np.sqrt((xr / a) ** 2 + (yr / b) ** 2) / wobble

    land = np.clip(1 - r, 0, 1)
    dist_c = np.sqrt(X ** 2 + Y ** 2)
    z = 1520 * land ** 1.15 + 430 * np.exp(-(dist_c / 2300) ** 2)

    rng = np.random.default_rng(7)
    for _ in range(70):  # 오름 (작은 기생 화산)
        t, rr = rng.uniform(0, 2 * math.pi), math.sqrt(rng.uniform(0.02, 0.85))
        ox, oy = rr * a * math.cos(t), rr * b * math.sin(t)
        cx, cy = ox * math.cos(th) - oy * math.sin(th), ox * math.sin(th) + oy * math.cos(th)
        h, rad = rng.uniform(40, 160), rng.uniform(250, 650)
        z += h * np.exp(-(((X - cx) ** 2 + (Y - cy) ** 2) / rad ** 2)) * (r < 0.97)

    sx, sy = 0.985 * a * math.cos(th), 0.985 * a * math.sin(th)  # 성산일출봉 (동쪽 끝)
    z += 180 * np.exp(-(((X - sx) ** 2 + (Y - sy) ** 2) / 600 ** 2))

    sea = r >= 1
    z[sea] = -np.clip((r[sea] - 1) * 8000, 6, SEA_DEPTH_M)  # 해안에서 빠르게 깊어짐 (파도 사이로 바닥이 보이지 않게)
    return z.astype(np.float32)


def load_dem(path):
    img = bpy.data.images.load(path, check_existing=True)
    try:
        img.colorspace_settings.name = "Non-Color"
    except TypeError:
        pass
    w, h = img.size
    ch = img.channels
    px = np.empty(w * h * ch, dtype=np.float32)
    img.pixels.foreach_get(px)
    z = px[0::ch].reshape(h, w)  # 맨 아래 줄이 남쪽 (Blender 이미지 기준)

    if np.nanmax(z) <= 1.0001:  # 0~1로 정규화된 이미지
        z = z * DEM_NORMALIZED_MAX_M
    z = np.nan_to_num(z, nan=-SEA_DEPTH_M)
    z[z < DEM_NODATA_BELOW_M] = -SEA_DEPTH_M
    z[z <= 0.5] = -SEA_DEPTH_M * 0.5  # 해수면 이하 → 바닷속 (파도 사이로 바닥이 보이지 않게 깊게)

    step = max(1, math.ceil(max(w, h) / GRID_MAX))
    log(f"DEM 불러옴: {w}×{h}, {step}칸마다 1점 사용, 최고 {float(z.max()):.0f} m")
    return z[::step, ::step].astype(np.float32)


def get_heights():
    if DEM_PATH and os.path.isfile(DEM_PATH):
        return load_dem(DEM_PATH), "DEM"
    if DEM_PATH:
        log(f"⚠ DEM 파일을 찾지 못해 가상 지형을 씁니다: {DEM_PATH}")
    cols = GRID_MAX
    rows = max(2, round(GRID_MAX * DEM_HEIGHT_M / DEM_WIDTH_M))
    return synthetic_jeju(cols, rows), "가상 지형"


# =====================================================================
# 4. 지형 메시 + 머티리얼
# =====================================================================

def build_terrain(z_m):
    rows, cols = z_m.shape
    xs = np.linspace(-0.5, 0.5, cols, dtype=np.float32) * DEM_WIDTH_M / M_PER_BU
    ys = np.linspace(-0.5, 0.5, rows, dtype=np.float32) * DEM_HEIGHT_M / M_PER_BU
    X, Y = np.meshgrid(xs, ys)
    Z = z_m * VERTICAL_EXAGGERATION / M_PER_BU
    verts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()]).astype(np.float32)

    idx = np.arange(rows * cols, dtype=np.int32).reshape(rows, cols)
    quads = np.column_stack([idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(),
                             idx[1:, 1:].ravel(), idx[1:, :-1].ravel()]).astype(np.int32)

    me = bpy.data.meshes.new("JejuTerrain")
    try:  # 빠른 방법
        me.vertices.add(len(verts))
        me.vertices.foreach_set("co", verts.ravel())
        me.loops.add(quads.size)
        me.loops.foreach_set("vertex_index", quads.ravel())
        me.polygons.add(len(quads))
        me.polygons.foreach_set("loop_start", np.arange(0, quads.size, 4, dtype=np.int32))
        me.update(calc_edges=True)
        me.validate()
    except Exception:  # 오래된 버전용
        me.clear_geometry()
        me.from_pydata(verts.tolist(), [], quads.tolist())
        me.update()
    if hasattr(me, "shade_smooth"):
        me.shade_smooth()
    else:
        me.polygons.foreach_set("use_smooth", np.ones(len(me.polygons), dtype=bool))

    ob = link_object(bpy.data.objects.new("JejuTerrain", me))
    ob.data.materials.append(terrain_material(float(Z.max())))
    log(f"지형 메시: {cols}×{rows} 점, 최고 {float(Z.max()):.0f} m(과장 {VERTICAL_EXAGGERATION}배)")
    return ob, xs, ys, Z


def terrain_material(max_z):
    mat = bpy.data.materials.new("JejuLand")
    nt = fresh_node_tree(mat)
    N, L = nt.nodes, nt.links

    out = N.new("ShaderNodeOutputMaterial"); out.location = (900, 0)
    bsdf = N.new("ShaderNodeBsdfPrincipled"); bsdf.location = (600, 0)
    set_input(bsdf, "Roughness", 0.95)
    set_input(bsdf, ["Specular IOR Level", "Specular"], 0.05)  # 땅은 거의 반사하지 않음

    geo = N.new("ShaderNodeNewGeometry"); geo.location = (-800, 0)
    sep = N.new("ShaderNodeSeparateXYZ"); sep.location = (-600, 100)
    rng = N.new("ShaderNodeMapRange"); rng.location = (-400, 100)
    rng.inputs["From Min"].default_value = 0.0
    rng.inputs["From Max"].default_value = max_z
    ramp = N.new("ShaderNodeValToRGB"); ramp.location = (-150, 100)
    el = ramp.color_ramp.elements
    el[0].position, el[0].color = 0.0, (0.05, 0.05, 0.05, 1)      # 검은 현무암 해안
    el[1].position, el[1].color = 1.0, (0.62, 0.62, 0.60, 1)      # 정상 암석
    for pos, col in ((0.004, (0.07, 0.13, 0.035, 1)),             # 해안 초지·밭
                     (0.20, (0.025, 0.075, 0.02, 1)),             # 숲
                     (0.55, (0.04, 0.07, 0.03, 1)),               # 고지대 숲
                     (0.80, (0.16, 0.14, 0.11, 1))):              # 관목·암석
        e = el.new(pos); e.color = col

    # 경사가 급한 곳은 어두운 현무암 색
    sep_n = N.new("ShaderNodeSeparateXYZ"); sep_n.location = (-600, -150)
    slope = N.new("ShaderNodeMapRange"); slope.location = (-400, -150)
    slope.inputs["From Min"].default_value = 0.92
    slope.inputs["From Max"].default_value = 0.70
    mix = N.new("ShaderNodeMix"); mix.location = (250, 0)
    mix.data_type = "RGBA"
    socket(mix.inputs, "B", "RGBA").default_value = (0.08, 0.07, 0.065, 1)

    L.new(geo.outputs["Position"], sep.inputs[0])
    L.new(sep.outputs["Z"], rng.inputs["Value"])
    L.new(rng.outputs["Result"], ramp.inputs["Fac"])
    L.new(geo.outputs["Normal"], sep_n.inputs[0])
    L.new(sep_n.outputs["Z"], slope.inputs["Value"])
    L.new(slope.outputs["Result"], socket(mix.inputs, "Factor", "VALUE"))
    L.new(ramp.outputs["Color"], socket(mix.inputs, "A", "RGBA"))
    L.new(socket(mix.outputs, "Result", "RGBA"), bsdf.inputs["Base Color"])
    L.new(bsdf.outputs[0], out.inputs["Surface"])
    return mat


# =====================================================================
# 5. 바다
# =====================================================================

def ocean_material():
    mat = bpy.data.materials.new("JejuSea")
    nt = fresh_node_tree(mat)
    N, L = nt.nodes, nt.links

    out = N.new("ShaderNodeOutputMaterial"); out.location = (900, 0)
    bsdf = N.new("ShaderNodeBsdfPrincipled"); bsdf.location = (600, 0)
    set_input(bsdf, "Roughness", 0.035)
    set_input(bsdf, "IOR", 1.333)
    set_input(bsdf, ["Specular IOR Level", "Specular"], 0.6)

    # 물거품: Ocean 모디파이어가 만든 'foam' 속성을 흰색으로 섞음
    foam = N.new("ShaderNodeAttribute"); foam.location = (-500, 250)
    foam.attribute_name = "foam"
    fr = N.new("ShaderNodeValToRGB"); fr.location = (-300, 250)
    fr.color_ramp.elements[0].position = 0.62
    fr.color_ramp.elements[1].position = 1.0
    fr.color_ramp.elements[1].color = (0.7, 0.7, 0.7, 1)  # 거품은 파도 끝에만 살짝
    mix = N.new("ShaderNodeMix"); mix.location = (300, 150)
    mix.data_type = "RGBA"
    socket(mix.inputs, "A", "RGBA").default_value = (0.004, 0.035, 0.055, 1)  # 깊은 제주 바다색
    socket(mix.inputs, "B", "RGBA").default_value = (0.85, 0.88, 0.9, 1)
    L.new(foam.outputs["Fac"], fr.inputs["Fac"])
    L.new(fr.outputs["Color"], socket(mix.inputs, "Factor", "VALUE"))
    L.new(socket(mix.outputs, "Result", "RGBA"), bsdf.inputs["Base Color"])

    # 잔물결: 시간에 따라 움직이는 4D 노이즈 → 법선(Bump)
    tc = N.new("ShaderNodeTexCoord"); tc.location = (-700, -150)
    noise = N.new("ShaderNodeTexNoise"); noise.location = (-450, -150)
    noise.noise_dimensions = "4D"
    noise.inputs["Scale"].default_value = 0.35
    noise.inputs["Detail"].default_value = 8.0
    bump = N.new("ShaderNodeBump"); bump.location = (250, -200)
    bump.inputs["Strength"].default_value = 0.12
    bump.inputs["Distance"].default_value = 0.4
    L.new(tc.outputs["Object"], noise.inputs["Vector"])
    L.new(noise.outputs["Fac"], bump.inputs["Height"])
    L.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    L.new(bsdf.outputs[0], out.inputs["Surface"])

    w = noise.inputs["W"]
    w.default_value = 0.0
    w.keyframe_insert("default_value", frame=1)
    w.default_value = FRAME_END / FPS * 0.35
    w.keyframe_insert("default_value", frame=FRAME_END)
    return mat


def quad_mesh(name, size):
    s = size / 2
    me = bpy.data.meshes.new(name)
    me.from_pydata([(-s, -s, 0), (s, -s, 0), (s, s, 0), (-s, s, 0)], [], [(0, 1, 2, 3)])
    return me


def build_ocean(mat, center):
    # (1) 카메라 앞의 실제 물결 구역
    ob = link_object(bpy.data.objects.new("Ocean_Waves", quad_mesh("Ocean_Waves", 10)))
    mod = ob.modifiers.new("Ocean", "OCEAN")
    mod.geometry_mode = "GENERATE"
    mod.spatial_size = OCEAN_TILE_M
    mod.repeat_x = OCEAN_REPEAT
    mod.repeat_y = OCEAN_REPEAT
    mod.resolution = OCEAN_RESOLUTION
    set_if(mod, "viewport_resolution", OCEAN_VIEWPORT_RES)
    mod.wind_velocity = OCEAN_WIND_MS
    mod.choppiness = OCEAN_CHOPPINESS
    mod.wave_scale = 1.0
    mod.wave_alignment = 0.35
    mod.wave_direction = math.radians(20)  # 남서쪽에서 해안으로 밀려오는 물결
    set_if(mod, "depth", 200)
    mod.use_normals = True
    mod.use_foam = True
    mod.foam_layer_name = "foam"
    mod.foam_coverage = 0.1
    mod.random_seed = 7
    mod.time = 0.0
    mod.keyframe_insert("time", frame=1)
    mod.time = FRAME_END / FPS * OCEAN_TIME_SPEED
    mod.keyframe_insert("time", frame=FRAME_END)
    ob.data.materials.append(mat)

    # 생성된 물결 구역의 중심을 원하는 위치에 맞춤
    bpy.context.view_layer.update()
    ev = ob.evaluated_get(bpy.context.evaluated_depsgraph_get())
    bb = [Vector(c) for c in ev.bound_box]
    mid = sum(bb, Vector()) / 8
    ob.location = Vector((center.x - mid.x, center.y - mid.y, 0))
    size = max(c.x for c in bb) - min(c.x for c in bb)
    log(f"물결 구역: {size:.0f} m × {size:.0f} m, 정점 {len(ev.data.vertices):,}개")

    # (2) 수평선까지 이어지는 먼 바다 (물결 구역 아래로 살짝 낮게)
    far = link_object(bpy.data.objects.new("Ocean_Far", quad_mesh("Ocean_Far", 40_000_000)))  # 높은 곳에서도 수평선 끝까지 덮도록 (40,000 km)
    far.location = (center.x, center.y, -1.5)
    far.data.materials.append(mat)
    return ob, far


# =====================================================================
# 6. 하늘 + 태양 (애니메이션)
# =====================================================================

def build_sky_and_sun():
    scene = bpy.context.scene
    world = bpy.data.worlds.new("JejuSky")
    scene.world = world
    nt = fresh_node_tree(world)
    sky = nt.nodes.new("ShaderNodeTexSky"); sky.location = (-300, 0)
    for t in ("MULTIPLE_SCATTERING", "NISHITA", "SINGLE_SCATTERING"):
        if set_if(sky, "sky_type", t):
            break
    set_if(sky, "sun_disc", True)
    set_if(sky, "sun_size", math.radians(0.9))
    set_if(sky, "altitude", 30)
    set_if(sky, "air_density", 1.0)
    if not set_if(sky, "aerosol_density", 1.0):
        set_if(sky, "dust_density", 1.0)
    set_if(sky, "ozone_density", 1.0)
    bg = nt.nodes.new("ShaderNodeBackground"); bg.location = (0, 0)
    bg.inputs["Strength"].default_value = 0.3
    wo = nt.nodes.new("ShaderNodeOutputWorld"); wo.location = (250, 0)
    nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], wo.inputs["Surface"])
    light = bpy.data.lights.new("Sun", "SUN")
    light.angle = math.radians(0.9)
    sun = link_object(bpy.data.objects.new("Sun", light))

    prev = Euler((0, 0, 0))
    step = 6
    frames = list(range(1, FRAME_END + 1, step))
    if frames[-1] != FRAME_END:
        frames.append(FRAME_END)
    for f in frames:
        t = (f - 1) / max(1, FRAME_END - 1)
        ease = 0.5 - 0.5 * math.cos(math.pi * t)  # 부드럽게 시작·정지
        az = SUN_AZIMUTH_START + (SUN_AZIMUTH_END - SUN_AZIMUTH_START) * t
        el = SUN_ELEVATION_START + (SUN_ELEVATION_END - SUN_ELEVATION_START) * ease
        d = sun_direction(az, el)

        # 태양 램프: +Z 축이 태양 쪽을 보게 (빛은 -Z 방향으로 나감)
        prev = d.to_track_quat("Z", "Y").to_euler("XYZ", prev)
        sun.rotation_euler = prev
        sun.keyframe_insert("rotation_euler", frame=f)

        # 해가 낮을수록 주황빛, 약하게
        k = max(0.0, min(1.0, (el - 1) / 20))
        light.color = (1.0, 0.55 + 0.4 * k, 0.32 + 0.58 * k)
        light.energy = SUN_LAMP_ENERGY * (0.35 + 0.65 * k)
        light.keyframe_insert("color", frame=f)
        light.keyframe_insert("energy", frame=f)

        # 하늘 속 태양: sun_rotation 은 북 = 0°, 동 = 90° (방위각과 같음)
        sky.sun_elevation = math.radians(el)
        sky.sun_rotation = math.radians(az)
        sky.keyframe_insert("sun_elevation", frame=f)
        sky.keyframe_insert("sun_rotation", frame=f)

        # 해가 높을 때 하늘이 너무 밝아지지 않도록 밝기 조절
        bg.inputs["Strength"].default_value = 0.1 + 0.2 * (1 - k)
        bg.inputs["Strength"].keyframe_insert("default_value", frame=f)
    return sun, sky


# =====================================================================
# 7. 카메라
# =====================================================================

def make_camera(name, loc, az_deg, pitch_deg=0.0, lens=CAMERA_LENS_MM):
    cam = bpy.data.cameras.new(name)
    cam.lens = lens
    cam.clip_start = 0.5
    cam.clip_end = 30_000_000  # 높은 곳에서도 수평선까지 보이도록
    ob = link_object(bpy.data.objects.new(name, cam))
    ob.location = loc
    # rot X 90° = 북쪽(+Y)을 봄. Z 회전으로 방위각 맞춤 (시계 방향 = 음수)
    ob.rotation_euler = (math.radians(90 + pitch_deg), 0, -math.radians(az_deg))
    return ob


def find_coast(xs, ys, Z, x_m):
    """x_m 위치에서 남쪽 바다 → 북쪽으로 올라가며 처음 만나는 육지(해안선)의 y."""
    j = int(np.argmin(np.abs(xs - x_m / M_PER_BU)))
    land = np.nonzero(Z[:, j] > 1.0)[0]
    if len(land) == 0:
        return float(xs[j]), float(ys[0] * 0.6)
    return float(xs[j]), float(ys[land[0]])


# =====================================================================
# 8. 실행
# =====================================================================

def main():
    scene = bpy.context.scene
    prefs = bpy.context.preferences.edit
    old_interp = prefs.keyframe_new_interpolation_type
    prefs.keyframe_new_interpolation_type = "LINEAR"
    try:
        clear_scene()
        scene.render.fps = FPS
        scene.frame_start = 1
        scene.frame_end = FRAME_END
        scene.frame_set(1)

        z_m, source = get_heights()
        log(f"지형 데이터: {source}")
        terrain, xs, ys, Z = build_terrain(z_m)

        # 해안 카메라 위치 (남쪽 해안선에서 바다 쪽으로)
        cx, coast_y = find_coast(xs, ys, Z, CAMERA_COAST_X_M)
        cam_loc = Vector((cx, coast_y - CAMERA_OFFSHORE_M / M_PER_BU, CAMERA_HEIGHT_M / M_PER_BU))
        look = sun_direction(CAMERA_AZIMUTH, 0)

        sea = ocean_material()
        wave_center = cam_loc + look * (OCEAN_TILE_M * OCEAN_REPEAT * 0.42)
        build_ocean(sea, wave_center)
        build_sky_and_sun()

        cam_coast = make_camera("Cam_Coast", cam_loc, CAMERA_AZIMUTH, CAMERA_PITCH)

        # 섬 북쪽 상공에서 남쪽을 바라보기: 섬 전체 + 남쪽 바다 + 해 질 녘 태양
        make_camera("Cam_Island", (12_000, 30_000, 9_000), 215, -13, lens=24)

        scene.camera = cam_coast

        # 렌더 설정
        try:
            scene.render.engine = RENDER_ENGINE
        except TypeError:
            scene.render.engine = "BLENDER_EEVEE"
        if scene.render.engine == "CYCLES":
            scene.cycles.samples = RENDER_SAMPLES
            scene.cycles.preview_samples = 32
            set_if(scene.cycles, "use_denoising", True)
        scene.render.resolution_x, scene.render.resolution_y = RESOLUTION
        scene.render.resolution_percentage = RESOLUTION_PERCENT
        for vt in ("AgX", "Filmic"):
            if set_if(scene.view_settings, "view_transform", vt):
                break
        set_if(scene.view_settings, "look", "AgX - Base Contrast")

        base = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else os.path.expanduser("~")
        out_dir = os.path.join(base, "jeju_render")
        scene.render.filepath = os.path.join(out_dir, OUTPUT_NAME)
        img = scene.render.image_settings
        try:
            set_if(img, "media_type", "VIDEO")
            img.file_format = "FFMPEG"
            scene.render.ffmpeg.format = "MPEG4"
            scene.render.ffmpeg.codec = "H264"
            scene.render.ffmpeg.constant_rate_factor = "HIGH"
            out_kind = "MP4 동영상"
        except (TypeError, AttributeError):
            set_if(img, "media_type", "IMAGE")
            img.file_format = "PNG"
            out_kind = "PNG 연속 이미지"

        # 뷰포트를 카메라 시점 + 렌더 미리보기로 (화면이 있을 때만)
        for area in getattr(bpy.context.screen, "areas", []):
            if area.type == "VIEW_3D":
                for sp in area.spaces:
                    if sp.type == "VIEW_3D":
                        sp.region_3d.view_perspective = "CAMERA"
                        sp.clip_end = 1_000_000
                        sp.shading.type = "MATERIAL"

        log("완료! ▶ 스페이스바로 재생, F12 한 장 렌더, Ctrl+F12 애니메이션 렌더")
        log(f"카메라 위치 {tuple(round(v) for v in cam_loc)}, 출력: {out_dir} ({out_kind})")
    finally:
        prefs.keyframe_new_interpolation_type = old_interp


main()
