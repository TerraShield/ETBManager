
#!/usr/bin/env python3
"""etb_map_builder.py — génère une carte 2D interactive depuis un export FModel.

Usage :
    python etb_map_builder.py Level0.json
    python etb_map_builder.py BoilerRoom.json --title "Boiler Room"
    python etb_map_builder.py
    python etb_map_builder.py ParkingGarage.json --meshes SM_WallExterior_A.zip

Les collisions connues sont chargées depuis wall_database.json, livré avec ce module.
Aucun dossier d'exports n'est parcouru pendant une génération normale.
--meshes (zip, dossier ou .json, répétable) ajoute ponctuellement des exports FModel
au calcul courant, sans modifier le catalogue intégré.
"""

import argparse
import collections
import hashlib
import json
import math
import os
import re
import sys
import webbrowser


# ------------------------------------------------------------------
# MATHS

def quat(p, y, r):
    p, y, r = (math.radians(v) / 2 for v in (p, y, r))
    SP, CP, SY, CY, SR, CR = math.sin(p), math.cos(p), math.sin(y), math.cos(y), math.sin(r), math.cos(r)
    return (
        CR*SP*SY - SR*CP*CY,
        -CR*SP*CY - SR*CP*SY,
        CR*CP*SY - SR*SP*CY,
        CR*CP*CY + SR*SP*SY
    )


def qmat(q):
    x, y, z, w = q
    n = math.sqrt(x*x + y*y + z*z + w*w) or 1.0
    x, y, z, w = x/n, y/n, z/n, w/n
    return [
        [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
        [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]
    ]


def mm(a, b):
    return [
        [
            sum(a[i][k]*b[k][j] for k in range(3))
            for j in range(3)
        ]
        for i in range(3)
    ]


def mv(a, v):
    return [
        sum(a[i][k]*v[k] for k in range(3))
        for i in range(3)
    ]


IDENT = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]


def vec(v, df):
    return (
        [v['X'], v['Y'], v['Z']]
        if isinstance(v, dict) and 'X' in v
        else list(df)
    )


# ------------------------------------------------------------------
# LECTURE DU NIVEAU

class Level:
    def __init__(self, path):
        with open(path, encoding='utf-8') as f:
            data = json.load(f)

        if isinstance(data, dict):
            for k in ('Exports', 'exports', 'objects', 'Objects'):
                if isinstance(data.get(k), list):
                    data = data[k]
                    break

        if not isinstance(data, list):
            raise SystemExit(
                "Format non reconnu : un export FModel doit être une liste d'objets JSON."
            )

        self.d = data
        self.cache = {}

    @staticmethod
    def idx(ref):
        try:
            return int(ref['ObjectPath'].rsplit('.', 1)[1])
        except Exception:
            return None

    def obj(self, i):
        return (
            self.d[i]
            if i is not None
            and 0 <= i < len(self.d)
            and isinstance(self.d[i], dict)
            else None
        )

    def world(self, i, depth=0):
        """Transformée monde d'un composant."""
        if i in self.cache:
            return self.cache[i]

        p = (self.obj(i) or {}).get('Properties') or {}

        loc = vec(p.get('RelativeLocation'), [0, 0, 0])
        sc = vec(p.get('RelativeScale3D'), [1, 1, 1])

        rr = p.get('RelativeRotation')

        R = (
            qmat(quat(rr['Pitch'], rr['Yaw'], rr['Roll']))
            if isinstance(rr, dict) and 'Yaw' in rr
            else IDENT
        )

        par = self.idx(p.get('AttachParent'))

        if par is not None and par != i and depth < 12 and self.obj(par):
            Lp, Rp, Sp = self.world(par, depth + 1)

            loc = [
                a + b
                for a, b in zip(
                    Lp,
                    mv(Rp, [Sp[k]*loc[k] for k in range(3)])
                )
            ]

            R = mm(Rp, R)
            sc = [Sp[k]*sc[k] for k in range(3)]

        self.cache[i] = (loc, R, sc)
        return self.cache[i]


def mesh_name(p):
    m = (p.get('StaticMesh') or {}) if isinstance(p, dict) else {}

    s = m.get('ObjectName') or m.get('ObjectPath') or ''

    if "'" in s:
        s = s.split("'")[1]

    return s.rsplit('.', 1)[-1].rsplit('/', 1)[-1]


def collect_placements(lv):
    """Tous les meshes posés : StaticMeshComponent + instances ISM/HISM."""
    out = []

    for i, o in enumerate(lv.d):
        if not isinstance(o, dict):
            continue

        t = o.get('Type')
        p = o.get('Properties') or {}

        if t not in (
            'StaticMeshComponent',
            'InstancedStaticMeshComponent',
            'HierarchicalInstancedStaticMeshComponent'
        ):
            continue

        m = mesh_name(p)

        if not m:
            act = lv.obj(lv.idx(o.get('Outer')))
            m = mesh_name((act or {}).get('Properties') or {}) if act else ''

        if not m:
            continue

        L, R, S = lv.world(i)
        inst = o.get('PerInstanceSMData')

        if inst:
            for it in inst:
                td = it.get('TransformData') or {}
                q = td.get('Rotation') or {}

                Ri = qmat((
                    q.get('X', 0),
                    q.get('Y', 0),
                    q.get('Z', 0),
                    q.get('W', 1)
                ))

                tr = vec(td.get('Translation'), [0, 0, 0])
                sc = vec(td.get('Scale3D'), [1, 1, 1])

                pos = [
                    a + b
                    for a, b in zip(
                        L,
                        mv(R, [S[k]*tr[k] for k in range(3)])
                    )
                ]

                Rw = mm(R, Ri)

                out.append(dict(
                    mesh=m,
                    pos=pos,
                    ax=[Rw[r][0] for r in range(3)],
                    ay=[Rw[r][1] for r in range(3)],
                    sc=[S[k]*sc[k] for k in range(3)]
                ))

        elif t == 'StaticMeshComponent':
            out.append(dict(
                mesh=m,
                pos=L,
                ax=[R[r][0] for r in range(3)],
                ay=[R[r][1] for r in range(3)],
                sc=S
            ))

    return out


# ------------------------------------------------------------------
# GROUPES DE MESHES

GROUPS = [
    ('Plinthes', '#566474', 0, 14, r'skirt|baseboard|plinth'),
    ('Lumières (meshes)', '#ffd166', 0, 14, r'light|lamp'),
    ('Portes / ouvertures', '#ff8a65', 1, 30, r'doorway|door|gate'),
    ('Murs', '#cfe0f0', 1, 30, r'wall|partition'),
    ('Sols', '#9fb3c8', 1, 0, r'floor|ground|slab'),
    ('Plafonds', '#7e8fa3', 0, 0, r'ceil'),
    ('Piliers / colonnes', '#ffe066', 1, 0, r'pillar|column|pole'),
    ('Escaliers / rampes', '#5fd0a0', 1, 0, r'stair|ramp'),
    ('Tuyaux / conduits', '#e8d44d', 0, 14, r'pipe|hose|duct|vent'),
    ('Clôtures / barrières', '#c792ea', 1, 14, r'fence|rail|barrier'),
    ('Décals', '#6b7785', 0, 0, r'decal'),
    ('Autres meshes', '#8fbcbb', 1, 0, r'.'),
]

LINE_GROUPS = {
    'Portes / ouvertures',
    'Murs',
    'Tuyaux / conduits',
    'Clôtures / barrières',
    'Plinthes'
}

TILE_GROUPS = {'Sols', 'Plafonds'}

NOT_LINEAR = re.compile(
    r'corner|bend|(^|_)t(_|$)|elbow|cross|junction|end'
)

LEN_RE = re.compile(r'(?:^|_)(\d+)m(?:_|$)', re.I)


def group_of(name):
    n = name.lower()

    for gi, g in enumerate(GROUPS):
        if re.search(g[4], n):
            return gi


def nominal_len(name, overrides):
    if name in overrides:
        return overrides[name]

    m = LEN_RE.search(name)

    if not m:
        return None

    s = m.group(1)

    return (
        float('0.' + s[1:])
        if len(s) > 1 and s[0] == '0'
        else float(s)
    ) * 100


def ang(v):
    return math.degrees(math.atan2(v[1], v[0]))


# ------------------------------------------------------------------
# DÉTECTION DES PIVOTS

def detect_line_pivot(items, tol=12.0):
    """Détecte l'axe et le pivot à partir des extrémités des meshes."""
    best, scores = None, {}

    for axis in ('X', 'Y'):
        for piv in ('start', 'center', 'end'):
            pts = []

            for it in items:
                if it['axis'] != axis:
                    continue

                u, ln, p = it['u'], it['len'], it['pos']

                o = {
                    'start': 0,
                    'center': -ln/2,
                    'end': -ln
                }[piv]

                p0 = (p[0] + u[0]*o, p[1] + u[1]*o)

                pts += [
                    p0,
                    (p0[0] + u[0]*ln, p0[1] + u[1]*ln)
                ]

            if len(pts) < 16:
                continue

            g = collections.defaultdict(list)

            for k, (x, y) in enumerate(pts):
                g[(round(x/25), round(y/25))].append(k)

            hit = 0

            for k, (x, y) in enumerate(pts):
                gx, gy = round(x/25), round(y/25)
                ok = False

                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for j in g.get((gx+dx, gy+dy), ()):
                            if (
                                j//2 != k//2
                                and math.hypot(
                                    pts[j][0]-x,
                                    pts[j][1]-y
                                ) <= tol
                            ):
                                ok = True
                                break

                        if ok:
                            break

                    if ok:
                        break

                hit += ok

            scores[(axis, piv)] = hit / len(pts)

    if scores:
        best = max(scores, key=scores.get)

    return best, scores


def tile_overlap(tiles, mode, step=100):
    cnt = collections.Counter()

    for t in tiles:
        w, h, ax, ay, p = t

        c = (
            (p[0], p[1])
            if mode == 'center'
            else (
                p[0] + ax[0]*w/2 + ay[0]*h/2,
                p[1] + ax[1]*w/2 + ay[1]*h/2
            )
        )

        zk = round(p[2] / 50)

        r = (
            abs(ax[0])*w/2 + abs(ay[0])*h/2,
            abs(ax[1])*w/2 + abs(ay[1])*h/2
        )

        x0, x1 = int((c[0]-r[0]) // step), int((c[0]+r[0]) // step)
        y0, y1 = int((c[1]-r[1]) // step), int((c[1]+r[1]) // step)

        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                dx = (gx + .5)*step - c[0]
                dy = (gy + .5)*step - c[1]

                if (
                    abs(dx*ax[0] + dy*ax[1]) <= w/2
                    and abs(dx*ay[0] + dy*ay[1]) <= h/2
                ):
                    cnt[(zk, gx, gy)] += 1

    return sum(1 for v in cnt.values() if v > 1)


# ------------------------------------------------------------------
# ACTEURS

NOISY = re.compile(
    r'ReflectionCapture|Decal|NavMesh|Recast|LightmassImportance|PostProcess|AmbientSound|Emitter|LevelSequence|InstancedFoliage|'
    r'WorldSettings|Brush|Volume$|Fog|SkyAtmosphere|SkyLight|DirectionalLight|^Info$|Manager|AudioVolume|Landscape|^Actor$|TextRender|Note$|Chunk',
    re.I
)

SHAPES = [
    (r'PlayerStart|Spawn', 'd'),
    (r'Zone|Trigger|Volume|Heat', 'r'),
    (r'Door|Valve|Switch|Button|Lever', 's')
]

PALETTE = [
    '#35b7ff', '#ff5252', '#ffe066', '#c792ea',
    '#5fd0a0', '#ff80ab', '#00e5ff', '#ffb86b',
    '#b0bec5', '#a5d6a7', '#f48fb1', '#80cbc4',
    '#e6ee9c', '#ce93d8'
]


def color_for(name):
    return PALETTE[
        int(hashlib.md5(name.encode()).hexdigest(), 16) % len(PALETTE)
    ]


def collect_actors(lv):
    acts = []

    for i, o in enumerate(lv.d):
        if not isinstance(o, dict) or o.get('Type') in (None, 'StaticMeshActor'):
            continue

        out = (
            (o.get('Outer') or {}).get('ObjectName', '')
            if isinstance(o.get('Outer'), dict)
            else ''
        )

        p = o.get('Properties') or {}

        if 'PersistentLevel' not in out or 'RootComponent' not in p:
            continue

        rc = lv.idx(p['RootComponent'])

        if rc is None or not lv.obj(rc):
            continue

        L, R, S = lv.world(rc)

        acts.append((i, o['Type'], L, R))

    return acts


def collect_volumes(lv, actor_ids):
    vols = []

    for i, o in enumerate(lv.d):
        if not isinstance(o, dict):
            continue

        if o.get('Type') not in (
            'BoxComponent',
            'SphereComponent',
            'CapsuleComponent'
        ):
            continue

        a = lv.idx(o.get('Outer'))

        if a not in actor_ids:
            continue

        p = o.get('Properties') or {}

        L, R, S = lv.world(i)
        t = o['Type']

        if t == 'BoxComponent' and isinstance(p.get('BoxExtent'), dict):
            e = p['BoxExtent']

            vols.append((
                a,
                'b',
                L,
                R,
                2*e['X']*S[0],
                2*e['Y']*S[1]
            ))

        elif t == 'SphereComponent' and 'SphereRadius' in p:
            vols.append((
                a,
                'c',
                L,
                R,
                p['SphereRadius']*max(S[0], S[1]),
                0
            ))

        elif t == 'CapsuleComponent' and 'CapsuleRadius' in p:
            vols.append((
                a,
                'c',
                L,
                R,
                p['CapsuleRadius']*max(S[0], S[1]),
                0
            ))

    return vols


# ------------------------------------------------------------------
# CONSTRUCTION DE LA CARTE

# ------------------------------------------------------------------
# EMPREINTES DE COLLISION DES MESHES
# (exports FModel de StaticMesh : BodySetup/AggGeom, sinon ExtendedBounds)

import glob
import zipfile

BLOCK_GROUPS = {
    'Murs',
    'Portes / ouvertures',
    'Clôtures / barrières',
    'Piliers / colonnes'
}

# Une pièce de collision gêne la marche si elle touche le sol (bas <= 50)
# et dépasse la hauteur de marche (haut >= 60). Les linteaux ne gênent pas,
# les allèges de fenêtre (haut ~100) bloquent.
GROUND_Z0_MAX = 50
GROUND_Z1_MIN = 60


def _hull2d(pts):
    pts = sorted(set((round(x, 2), round(y, 2)) for x, y in pts))

    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])

    lo = []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)

    up = []
    for p in reversed(pts):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)

    return lo[:-1] + up[:-1]


def _elem_polys(agg):
    """Éléments de collision -> [(contour 2D local, zmin, zmax)]."""
    out = []

    for b in agg.get('BoxElems') or []:
        c = b.get('Center') or {}
        r = b.get('Rotation') or {}
        R = qmat(quat(r.get('Pitch', 0), r.get('Yaw', 0), r.get('Roll', 0)))
        hx, hy, hz = b.get('X', 0)/2, b.get('Y', 0)/2, b.get('Z', 0)/2
        pts, zs = [], []

        for sx in (-1, 1):
            for sy in (-1, 1):
                for sz in (-1, 1):
                    v = mv(R, [sx*hx, sy*hy, sz*hz])
                    pts.append((c.get('X', 0) + v[0], c.get('Y', 0) + v[1]))
                    zs.append(c.get('Z', 0) + v[2])

        out.append((_hull2d(pts), min(zs), max(zs)))

    for cv in agg.get('ConvexElems') or []:
        vd = cv.get('VertexData') or []

        if not vd:
            continue

        tf = cv.get('Transform') or {}
        q = tf.get('Rotation') or {}
        R = qmat((q.get('X', 0), q.get('Y', 0), q.get('Z', 0), q.get('W', 1)))
        T = vec(tf.get('Translation'), [0, 0, 0])
        S = vec(tf.get('Scale3D'), [1, 1, 1])
        pts, zs = [], []

        for v in vd:
            w = mv(R, [v['X']*S[0], v['Y']*S[1], v['Z']*S[2]])
            pts.append((T[0] + w[0], T[1] + w[1]))
            zs.append(T[2] + w[2])

        out.append((_hull2d(pts), min(zs), max(zs)))

    for key in ('SphereElems', 'SphylElems'):
        for s in agg.get(key) or []:
            c = s.get('Center') or {}
            rad = s.get('Radius', 0)
            half = rad + (s.get('Length', 0)/2 if key == 'SphylElems' else 0)
            pts = [
                (c.get('X', 0) + rad*math.cos(a*math.pi/4),
                 c.get('Y', 0) + rad*math.sin(a*math.pi/4))
                for a in range(8)
            ]
            out.append((_hull2d(pts), c.get('Z', 0) - half, c.get('Z', 0) + half))

    return out


def _read_mesh_export(data, foot):
    """Import ponctuel d'un export FModel dans le cache mémoire."""
    if not isinstance(data, list):
        return

    body = next(
        (o for o in data if isinstance(o, dict) and o.get('Type') == 'BodySetup'),
        None
    )
    agg = ((body or {}).get('Properties') or {}).get('AggGeom') or {}

    for mesh in data:
        if not isinstance(mesh, dict) or mesh.get('Type') != 'StaticMesh':
            continue
        polys = [
            (h, z0, z1)
            for h, z0, z1 in _elem_polys(agg)
            if len(h) >= 3
        ]
        if not polys:
            bounds = (mesh.get('Properties') or {}).get('ExtendedBounds') or {}
            origin, extent = bounds.get('Origin'), bounds.get('BoxExtent')
            if origin and extent:
                hull = [
                    (origin['X'] + sx*extent['X'], origin['Y'] + sy*extent['Y'])
                    for sx in (-1, 1) for sy in (-1, 1)
                ]
                polys = [(_hull2d(hull), origin['Z'] - extent['Z'], origin['Z'] + extent['Z'])]
        if polys:
            foot[mesh['Name']] = polys


def load_footprints(sources, level_path, log):
    """Charge d'abord le catalogue intégré ; les exports ne sont lus que sur demande."""
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wall_database.json')
    foot = {}
    aliases = {}

    try:
        with open(db_path, encoding='utf-8') as fh:
            database = json.load(fh)
        geometries = database.get('geometries') or {}
        aliases = database.get('aliases') or {}
        for mesh_name_, geometry_id in aliases.items():
            geometry = geometries.get(geometry_id) or {}
            agg = geometry.get('agg_geom') or {}
            polys = [
                (h, z0, z1)
                for h, z0, z1 in _elem_polys(agg)
                if len(h) >= 3
            ]
            if polys:
                foot[mesh_name_] = polys
        log(
            f"Catalogue des murs chargé : {len(foot)} noms, "
            f"{len(geometries)} géométries uniques"
        )
    except FileNotFoundError:
        log("  ! Catalogue wall_database.json introuvable")
    except Exception as ex:
        log(f"  ! Erreur de lecture du catalogue : {ex}")

    # --meshes reste un mécanisme d'import ponctuel, jamais un scan implicite.
    paths = list(sources or [])
    if not paths:
        return foot

    import glob
    import zipfile

    imported = {}
    def add_file(raw):
        try:
            _read_mesh_export(json.loads(raw.decode('utf-8')), imported)
        except Exception as ex:
            log(f"  ! Export mesh ignoré : {ex}")

    for source in paths:
        try:
            if os.path.isdir(source):
                for filename in glob.glob(os.path.join(source, '**', '*.json'), recursive=True):
                    if os.path.getsize(filename) < 30_000_000:
                        with open(filename, 'rb') as fh:
                            add_file(fh.read())
            elif source.lower().endswith('.zip'):
                with zipfile.ZipFile(source) as archive:
                    for filename in archive.namelist():
                        if filename.lower().endswith('.json') and archive.getinfo(filename).file_size < 30_000_000:
                            add_file(archive.read(filename))
            else:
                with open(source, 'rb') as fh:
                    add_file(fh.read())
        except Exception as ex:
            log(f"  ! {source} : {ex}")

    foot.update(imported)
    if imported:
        log(f"Exports complémentaires chargés ponctuellement : {len(imported)} meshes")
    return foot


def footprint_world(e, polys):
    """Contours monde des éléments de collision qui bloquent la marche."""
    ax, ay, p, sc = e['ax'], e['ay'], e['pos'], e['sc']

    if abs(ax[2]) > .2 or abs(ay[2]) > .2:
        return None  # mesh couché/incliné : on ne devine pas

    out = []

    for hull, z0, z1 in polys:
        if not (z0*sc[2] <= GROUND_Z0_MAX and z1*sc[2] >= GROUND_Z1_MIN):
            continue

        out.append([
            (p[0] + ax[0]*sc[0]*x + ay[0]*sc[1]*y,
             p[1] + ax[1]*sc[0]*x + ay[1]*sc[1]*y)
            for x, y in hull
        ])

    return out or None


def build(path, args, log=print):
    lv = Level(path)

    log(f"Lecture de {os.path.basename(path)} : {len(lv.d)} objets")

    pl = collect_placements(lv)

    log(f"Meshes posés : {len(pl)}")

    foot = load_footprints(getattr(args, 'meshes', None), path, log)

    over = {}

    for s in args.len or []:
        k, v = s.split('=')
        over[k] = float(v)

    forced = {}

    for s in args.pivot or []:
        k, v = s.split('=')
        forced[k] = v

    # 1) Éléments linéaires / tuiles par groupe

    by_group = collections.defaultdict(list)

    for e in pl:
        e['g'] = group_of(e['mesh'])
        by_group[GROUPS[e['g']][0]].append(e)

    lines = collections.defaultdict(list)

    for gname in LINE_GROUPS:
        for e in by_group.get(gname, []):
            ln = nominal_len(e['mesh'], over)

            if ln is None or NOT_LINEAR.search(e['mesh'].lower()):
                continue

            for axis, u, sc in (
                ('X', e['ax'], e['sc'][0]),
                ('Y', e['ay'], e['sc'][1])
            ):
                if abs(u[2]) < 0.2:
                    e.setdefault('cand', {})[axis] = dict(
                        axis=axis,
                        u=u,
                        len=ln*sc,
                        pos=e['pos']
                    )

            if 'cand' in e:
                lines[gname].append(e)

    report = []
    pivots = {}
    base = None

    for gname in (
        'Murs',
        'Portes / ouvertures',
        'Tuyaux / conduits',
        'Clôtures / barrières',
        'Plinthes'
    ):
        its = lines.get(gname, [])

        if not its:
            continue

        if gname in forced:
            pivots[gname] = ('X', forced[gname])
            report.append(f"  {gname}: pivot forcé = {forced[gname]}")
            continue

        itemsX = [
            e['cand']['X']
            for e in its
            if 'X' in e['cand']
        ]

        itemsY = [
            e['cand']['Y']
            for e in its
            if 'Y' in e['cand']
        ]

        bestk, sc = detect_line_pivot(itemsX + itemsY)

        if bestk and sc[bestk] >= 0.30:
            pivots[gname] = bestk

            report.append(
                f"  {gname}: axe {bestk[0]}, pivot {bestk[1]} "
                f"(recoupement {sc[bestk]*100:.0f} %, {len(its)} pièces)"
            )

        elif base and gname != 'Murs':
            pivots[gname] = base

            report.append(
                f"  {gname}: pivot incertain, réutilise celui des murs {base}"
            )

        else:
            pivots[gname] = ('X', 'center')

            report.append(
                f"  {gname}: pivot incertain (meilleur {max(sc.values())*100:.0f} % seulement) -> centre par défaut ; utilise --pivot si besoin"
                if sc
                else f"  {gname}: trop peu de pièces pour détecter le pivot -> centre"
            )

        if gname == 'Murs':
            base = pivots[gname]

    for gname in ('Portes / ouvertures',):
        if gname not in pivots and base:
            pivots[gname] = base

    tiles_by = {}

    for gname in TILE_GROUPS:
        ts = []

        for e in by_group.get(gname, []):
            ln = nominal_len(e['mesh'], over)

            if ln and abs(e['ax'][2]) < .5 and abs(e['ay'][2]) < .5:
                e['tile'] = True
                ts.append(e)

        tiles_by[gname] = ts

    tpiv = args.tile_pivot

    if not tpiv:
        probe = tiles_by.get('Sols') or tiles_by.get('Plafonds') or []

        if len(probe) >= 4:
            tt = [
                (
                    nominal_len(e['mesh'], over)*e['sc'][0],
                    nominal_len(e['mesh'], over)*e['sc'][1],
                    e['ax'],
                    e['ay'],
                    e['pos']
                )
                for e in probe
            ]

            oc = tile_overlap(tt, 'center')
            ok_ = tile_overlap(tt, 'corner')

            if abs(oc - ok_) > 0.1 * max(oc, ok_, 1):
                tpiv = 'center' if oc < ok_ else 'corner'

                report.append(
                    f"  Sols/plafonds: pivot {tpiv} "
                    f"(recouvrements : centre {oc}, coin {ok_})"
                )

            else:
                wall_pos = []

                if 'Murs' in pivots:
                    axis, piv = pivots['Murs']

                    for e in lines.get('Murs', []):
                        c = e['cand'].get(axis)

                        if c:
                            o = {
                                'start': 0,
                                'center': -c['len']/2,
                                'end': -c['len']
                            }[piv]

                            wall_pos.append((
                                e['pos'][0] + c['u'][0]*(o + c['len']/2),
                                e['pos'][1] + c['u'][1]*(o + c['len']/2),
                                abs(c['u'][0]) > .9
                            ))

                def edge_score(mode):
                    ex, ey = set(), set()

                    for w, h, ax, ay, p in tt:
                        if abs(ax[0]) > .99:
                            cxp, cyp = (
                                (p[0], p[1])
                                if mode == 'center'
                                else (
                                    p[0] + ax[0]*w/2 + ay[0]*h/2,
                                    p[1] + ax[1]*w/2 + ay[1]*h/2
                                )
                            )

                            ex.update((
                                round((cxp - w/2)/10),
                                round((cxp + w/2)/10)
                            ))

                            ey.update((
                                round((cyp - h/2)/10),
                                round((cyp + h/2)/10)
                            ))

                    near = lambda v, S: any(
                        (round(v/10) + d) in S
                        for d in (-3, -2, -1, 0, 1, 2, 3)
                    )

                    return sum(
                        1
                        for x, y, horiz in wall_pos
                        if near(y if horiz else x, ey if horiz else ex)
                    )

                sc_c = edge_score('center')
                sc_k = edge_score('corner')

                tpiv = 'center' if sc_c >= sc_k else 'corner'

                report.append(
                    f"  Sols/plafonds: recouvrement non discriminant, "
                    f"alignement des murs sur les bords : centre {sc_c}, "
                    f"coin {sc_k} -> pivot {tpiv}"
                )

        else:
            tpiv = 'center'

    # 2) Couches d'altitude

    zs = collections.Counter()

    for gname in ('Murs', 'Portes / ouvertures', 'Sols'):
        for e in by_group.get(gname, []):
            zs[round(e['pos'][2] / 25) * 25] += 1

    clusters = []

    for z in sorted(zs):
        if clusters and z - clusters[-1][-1] <= 150:
            clusters[-1].append(z)
        else:
            clusters.append([z])

    cl = [
        (
            sum(z*zs[z] for z in c) / sum(zs[z] for z in c),
            sum(zs[z] for z in c)
        )
        for c in clusters
    ] or [(0, 1)]

    cl = sorted(sorted(cl, key=lambda t: -t[1])[:args.max_layers])

    LZ = [round(z) for z, _ in cl]

    counts = [0]*len(LZ)

    def layer_of(z):
        b = 0

        for i, v in enumerate(LZ):
            if v <= z + 50:
                b = i

        return b

    for gname in ('Murs', 'Portes / ouvertures', 'Sols'):
        for e in by_group.get(gname, []):
            counts[layer_of(e['pos'][2])] += 1

    LBL = [
        f"z ≈ {z:+d}  ({n} pièces)"
        for z, n in zip(LZ, counts)
    ]

    report.append(
        "  Calques d'altitude : " + ", ".join(f"{z:+d}" for z in LZ)
    )

    # 3) Catégories + éléments

    CATS = []
    GR = [g[0] for g in GROUPS]
    catidx = {}

    def cat(name, g, color, on, lw=0, s='c'):
        if name not in catidx:
            catidx[name] = len(CATS)

            CATS.append(dict(
                n=name,
                g=g,
                c=color,
                on=on,
                lw=lw,
                s=s,
                cnt=0,
                blk=False
            ))

        return catidx[name]

    Ls, Rs, Ds, Ks, Vs = [], [], [], [], []
    POLs = []
    seen = set()

    for e in pl:
        gname, gcol, gon, glw = (
            GROUPS[e['g']][0],
            GROUPS[e['g']][1],
            GROUPS[e['g']][2],
            GROUPS[e['g']][3]
        )

        ci = cat(e['mesh'], e['g'], gcol, gon, glw)

        z = e['pos'][2]
        ly = layer_of(z)
        placed = False

        # Meshes dont on connaît la collision : contour exact
        if e['mesh'] in foot:
            polys = footprint_world(e, foot[e['mesh']])

            if polys:
                if gname in BLOCK_GROUPS:
                    CATS[ci]['blk'] = True

                sx_ = sum(x for poly in polys for x, _ in poly)
                sy_ = sum(y for poly in polys for _, y in poly)
                npt = sum(len(poly) for poly in polys)

                k = ('POL', ci, round(sx_/npt/5), round(sy_/npt/5), round(z/10), npt)

                if k not in seen:
                    seen.add(k)

                    for poly in polys:
                        POLs.append([
                            ly,
                            ci,
                            [round(v) for pt in poly for v in pt]
                        ])

                    CATS[ci]['cnt'] += 1

                continue

        if gname in lines and 'cand' in e and gname in pivots:
            axis, piv = pivots[gname]
            c = e['cand'].get(axis)

            if c:
                ln, u = c['len'], c['u']

                o = {
                    'start': 0,
                    'center': -ln/2,
                    'end': -ln
                }[piv]

                cxp = e['pos'][0] + u[0]*(o + ln/2)
                cyp = e['pos'][1] + u[1]*(o + ln/2)

                k = (
                    ci,
                    round(cxp/5),
                    round(cyp/5),
                    round(ang(u) % 180),
                    round(ln),
                    ly
                )

                if k not in seen:
                    seen.add(k)

                    Ls.append([
                        round(cxp),
                        round(cyp),
                        round(ang(u) % 180, 1),
                        round(ln),
                        ly,
                        ci
                    ])

                    CATS[ci]['cnt'] += 1

                placed = True

        elif gname in TILE_GROUPS and e.get('tile'):
            ln = nominal_len(e['mesh'], over)

            w, h = ln*e['sc'][0], ln*e['sc'][1]

            ax, ay, p = e['ax'], e['ay'], e['pos']

            cxp, cyp = (
                (p[0], p[1])
                if tpiv == 'center'
                else (
                    p[0] + ax[0]*w/2 + ay[0]*h/2,
                    p[1] + ax[1]*w/2 + ay[1]*h/2
                )
            )

            k = (
                ci,
                round(cxp/5),
                round(cyp/5),
                round(ang(ax) % 90),
                round(w),
                round(h),
                ly
            )

            if k not in seen:
                seen.add(k)

                Rs.append([
                    round(cxp),
                    round(cyp),
                    ly,
                    round(w),
                    round(h),
                    round(ang(ax), 1),
                    ci
                ])

                CATS[ci]['cnt'] += 1

            placed = True

        if not placed:
            k = (
                ci,
                round(e['pos'][0]/5),
                round(e['pos'][1]/5),
                round(z/5)
            )

            if k not in seen:
                seen.add(k)

                Ds.append([
                    round(e['pos'][0]),
                    round(e['pos'][1]),
                    ly,
                    ci
                ])

                CATS[ci]['cnt'] += 1

    # Acteurs

    acts = collect_actors(lv)

    ids = {a[0] for a in acts}

    ago = len(GR)
    GR.append('Objets (acteurs)')

    for i, t, Lc, Rc in acts:
        nm = re.sub(r'_C$', '', t)

        shape = next(
            (s for rx, s in SHAPES if re.search(rx, nm, re.I)),
            'c'
        )

        ci = cat(
            nm,
            ago,
            color_for(nm),
            0 if NOISY.search(nm) else 1,
            0,
            shape
        )

        Ks.append([
            round(Lc[0]),
            round(Lc[1]),
            layer_of(Lc[2] - 100),
            ci,
            round(ang([Rc[0][0], Rc[1][0]]))
        ])

        CATS[ci]['cnt'] += 1

    ainfo = {
        i: cat(re.sub(r'_C$', '', t), ago, color_for(t), 1)
        for i, t, _, _ in acts
    }

    for a, kind, Lc, Rc, sa, sb in collect_volumes(lv, ids):
        if max(sa, sb) > 12000 or max(sa, sb) <= 0:
            continue

        ly = layer_of(Lc[2] - 100)

        if kind == 'c':
            Vs.append([
                round(Lc[0]),
                round(Lc[1]),
                ly,
                ainfo[a],
                'c',
                round(sa),
                0,
                0
            ])

        else:
            Vs.append([
                round(Lc[0]),
                round(Lc[1]),
                ly,
                ainfo[a],
                'b',
                round(sa),
                round(sb),
                round(ang([Rc[0][0], Rc[1][0]],), 1)
            ])

    used = (
        set(a[5] for a in Ls)
        | set(a[6] for a in Rs)
        | set(a[3] for a in Ds)
        | set(a[3] for a in Ks)
        | set(a[3] for a in Vs)
        | set(a[1] for a in POLs)
    )

    for i, c in enumerate(CATS):
        if i in used and c['cnt'] == 0:
            c['cnt'] = sum(1 for a in Vs if a[3] == i)

    log("Détection :")

    for r in report:
        log(r)

    log(
        f"Éléments : {len(Ls)} segments, {len(Rs)} tuiles, "
        f"{len(Ds)} points, {len(Ks)} objets, {len(Vs)} volumes, "
        f"{len(POLs)} contours de collision ; "
        f"{len(CATS)} catégories"
    )

    return dict(
        CATS=CATS,
        GR=GR,
        L=Ls,
        R=Rs,
        D=Ds,
        K=Ks,
        V=Vs,
        LBL=LBL,
        LZ=LZ,
        POL=POLs,
        MAIN=counts.index(max(counts)) if counts else 0
    )


# ==================================================================
# TEMPLATE HTML
# ==================================================================

TEMPLATE = r'''<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">

<style>
:root {
    --bg: #f4f6f8;
    --fg: #1c2530;
    --bar: #e4e9ee;
    --border: #c5cdd5;
    box-sizing: border-box;
}

@media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
        --bg: #0c0f13;
        --fg: #e6ebf0;
        --bar: #1b222b;
        --border: #39434e;
    }
}

:root[data-theme="dark"] {
    --bg: #0c0f13;
    --fg: #e6ebf0;
    --bar: #1b222b;
    --border: #39434e;
}

html, body {
    height: 100%;
    margin: 0;
    background: var(--bg);
    color: var(--fg);
    font: 14px system-ui, sans-serif;
}

body {
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

/* --------------------------------------------------
   BARRES D'OUTILS
-------------------------------------------------- */

#toolbars {
    flex: 0 0 auto;
    display: flex;
    flex-direction: column;
    background: var(--bar);
    border-bottom: 1px solid var(--border);
    z-index: 5;
}

.toolbar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 7px;
    padding: 7px 11px;
    border-bottom: 1px solid var(--border);
}

.toolbar:last-child {
    border-bottom: none;
}

.toolbar-title {
    font-weight: 700;
    font-size: 12px;
    min-width: 125px;
    opacity: .8;
    text-transform: uppercase;
    letter-spacing: .04em;
}

.toolbar button,
.toolbar select,
.toolbar input {
    font: inherit;
    font-size: 12px;
}

.toolbar button {
    padding: 5px 9px;
    cursor: pointer;
    border: 1px solid var(--border);
    border-radius: 5px;
    color: var(--fg);
    background: var(--bg);
}

.toolbar button:hover:not(:disabled) {
    filter: brightness(.94);
}

.toolbar button:disabled {
    opacity: .4;
    cursor: not-allowed;
}

.toolbar label {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    white-space: nowrap;
    font-size: 12px;
}

.toolbar input[type="checkbox"] {
    margin: 0;
}

.toolbar input[type="color"] {
    width: 30px;
    height: 24px;
    padding: 1px;
    border: 1px solid var(--border);
    border-radius: 4px;
    cursor: pointer;
    vertical-align: middle;
}

.toolbar-status {
    flex: 1 1 180px;
    min-width: 130px;
    font-size: 11px;
    opacity: .7;
}

#pathToggle.active,
#routeSelect.active {
    background: #2563a6;
    color: white;
    border-color: #2563a6;
}

#routeCalculate {
    background: #246d4b;
    color: white;
    border-color: #246d4b;
}

#routeExportHTML {
    background: #2563a6;
    color: white;
    border-color: #2563a6;
}

#pathHelp,
#routeStatus {
    overflow-wrap: anywhere;
}

/* --------------------------------------------------
   CARTE
-------------------------------------------------- */

#m {
    flex: 1;
    min-height: 0;
    display: flex;
}

#w {
    flex: 1;
    min-width: 0;
    position: relative;
    background: #11151b;
}

#cats {
    width: 270px;
    overflow: auto;
    background: var(--bar);
    padding: 8px 10px;
    box-sizing: border-box;
    font-size: 13px;
}

#cats input[type=search] {
    width: 100%;
    box-sizing: border-box;
    margin-bottom: 6px;
}

#cats details {
    margin: 4px 0;
}

#cats summary {
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 6px;
    font-weight: 600;
}

#cats .row {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 1px 0 1px 18px;
}

#cats .row span.n {
    margin-left: auto;
    opacity: .6;
    font-size: 11px;
}

#cats .sw {
    width: 10px;
    height: 10px;
    border-radius: 2px;
    flex: none;
}

#cats .nm {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

#pn {
    width: 320px;
    overflow: auto;
    background: var(--bar);
    padding: 10px 12px;
    font: 12px ui-monospace, Consolas, monospace;
    box-sizing: border-box;
}

#pn h3 {
    margin: 0 0 6px;
    font: 600 14px system-ui;
}

#pn .k {
    opacity: .6;
    margin-top: 9px;
    font: 11px system-ui;
}

#pn .v {
    word-break: break-all;
}

canvas {
    width: 100%;
    height: 100%;
    display: block;
    touch-action: none;
}

#hud {
    position: absolute;
    left: 8px;
    bottom: 8px;
    background: #000a;
    color: #e6ebf0;
    padding: 4px 8px;
    border-radius: 4px;
    font: 12px ui-monospace, monospace;
    line-height: 1.5;
    max-width: 80%;
    pointer-events: none;
}

#st {
    display: inline-block;
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: #e55;
    margin-right: 5px;
}

@media(max-width: 1000px) {
    .toolbar-title {
        min-width: 100%;
    }
}

@media(max-width: 800px) {
    body {
        overflow: auto;
        min-height: 100%;
    }

    #toolbars {
        position: relative;
    }

    #m {
        min-height: 65vh;
        flex-direction: column;
    }

    #w {
        min-height: 55vh;
    }

    #pn, #cats {
        width: auto;
        max-height: 25vh;
    }

    .toolbar-status {
        flex-basis: 100%;
    }
}
</style>
</head>

<body>

<!-- ======================================================
     1. AFFICHAGE ET NAVIGATION
====================================================== -->

<div id="toolbars">

<div class="toolbar" id="viewToolbar">
    <span class="toolbar-title">Affichage et navigation</span>

    <label class="lv">
        <input type="checkbox" id="fo" checked>
        Suivre le joueur
    </label>

    <label class="lv">
        <input type="checkbox" id="au" checked>
        Calque auto (selon Z)
    </label>

    <label class="lv">
        <input type="checkbox" id="tr" checked>
        Trace joueur
    </label>

    <label>
        Calque
        <select id="ly"></select>
    </label>

    <label>
        <input type="checkbox" id="vo" checked>
        Volumes
    </label>

    <label class="lv">
        <input type="checkbox" id="sim">
        Test : clic = téléporter
    </label>

    <label>
        Orientation
        <select id="o">
            <option value="0">Vue UE (X→, Y↓)</option>
            <option value="1">Y vers le haut</option>
        </select>
    </label>

    <button id="r">Recentrer</button>
    <button id="cl" class="lv">Effacer trace joueur</button>
</div>


<!-- ======================================================
     2. TRACÉ MANUEL
====================================================== -->

<div class="toolbar" id="traceToolbar">
    <span class="toolbar-title">Tracé manuel</span>

    <button id="pathToggle">Tracer (T)</button>

    <label>
        Couleur
        <input type="color" id="pathColor" value="#ff5252">
    </label>

    <button id="pathFinish" disabled>
        Terminer (Échap)
    </button>

    <button id="pathUndo" disabled>
        Annuler point
    </button>

    <button id="pathDelete" disabled>
        Supprimer segment
    </button>

    <button id="pathClear">
        Effacer tracé
    </button>

    <span id="pathHelp" class="toolbar-status">
        T pour commencer, T pour ajouter un segment, Échap pour terminer.
    </span>
</div>


<!-- ======================================================
     3. CHEMIN LE PLUS COURT
     Fonctionnement de calcul conservé.
====================================================== -->

<div class="toolbar" id="routeToolbar">
    <span class="toolbar-title">Chemin le plus court</span>

    <button id="routeSelect">
        Ajouter des points
    </button>

    <button id="routeCalculate">
        Calculer le plus court
    </button>

    <button id="routeUndo">
        Annuler point
    </button>

    <button id="routeClear">
        Effacer points
    </button>

    <label>
        Couleur
        <input type="color" id="routeColor" value="#00d5ff">
    </label>

    <span id="routeStatus" class="toolbar-status">
        Choisis plusieurs points sur la carte.
    </span>
</div>


<!-- ======================================================
     4. EXPORTATION
====================================================== -->

<div class="toolbar" id="exportToolbar">
    <span class="toolbar-title">Exportation</span>

    <button id="png">Exporter PNG</button>
    <button id="pathExport">Tracé JSON</button>
    <button id="routeExport">Itinéraire JSON</button>
    <button id="routeExportHTML">Exporter HTML complet</button>
</div>

</div>


<!-- ======================================================
     CARTE ET PANNEAUX
====================================================== -->

<div id="m">
    <aside id="cats"></aside>

    <div id="w">
        <canvas id="cv"></canvas>

        <div id="hud">
            <span id="st"></span>
            <span id="tx">__HUD0__</span>
        </div>
    </div>

    <aside id="pn"></aside>
</div>


<script>

// ======================================================
// DONNÉES DE LA CARTE
// ======================================================

const LIVE = __LIVE__;
const CATS = __CATS__;
const GR = __GR__;
const L = __L__;
const R = __R__;
const D = __D__;
const K = __K__;
const V = __V__;
const POL = __POL__;
const LBL = __LBL__;
const LZ = __LZ__;

const Q = new URLSearchParams(location.search);

const SRC =
    Q.get('src') ||
    (
        location.protocol == 'file:'
            ? 'http://127.0.0.1:8765/pos'
            : '/pos'
    );

const POLL = 50;
const ZOFF = +(Q.get('zoff') || 90);

const cv = document.getElementById('cv');
const cx = cv.getContext('2d');

const $ = id => document.getElementById(id);

const EMBEDDED = window.__ETB_EMBEDDED_STATE__ || {};

let s = .05;
let ox = 0;
let oy = 0;
let w = 0;
let h = 0;

let dr = null;
let pl = null;
let tr = [];
let last = 0;

let layer = LIVE ? __MAIN__ : -1;
let ON = CATS.map(c => c.on);
let raf = 0;

if (EMBEDDED.player) {
    pl = { ...EMBEDDED.player };
}

const fy = () => $('o').value == '1' ? -1 : 1;

const P = (x, y) => [
    ox + x*s,
    oy + fy()*y*s
];

const vis = l => layer < 0 || l == layer;


// ======================================================
// COORDONNÉES SOURIS -> COORDONNÉES CARTE
// ======================================================

function canvasToWorld(clientX, clientY) {
    const rect = cv.getBoundingClientRect();

    const canvasX =
        (clientX - rect.left) * (w / rect.width);

    const canvasY =
        (clientY - rect.top) * (h / rect.height);

    return {
        x: (canvasX - ox) / s,
        y: fy() * (canvasY - oy) / s
    };
}


// ======================================================
// TRACÉ MANUEL
// ======================================================

let pathSegments = [];
let pathPoints = [];

let pathDrawing = false;
let selectedPathSegment = -1;
let pathColor = '#ff5252';

let pathDraftStart = -1;

let lastTracePointer = null;

const PATH_STORAGE_KEY =
    'etb-manual-traces:' +
    location.pathname +
    ':' +
    document.title;


// ------------------------------------------------------
// SAUVEGARDE ET CHARGEMENT

function saveManualPath() {
    try {
        localStorage.setItem(
            PATH_STORAGE_KEY,
            JSON.stringify(pathSegments)
        );
    } catch (error) {
        console.warn(
            'Impossible de sauvegarder le tracé :',
            error
        );
    }
}


function loadManualPath() {
    const embedded = EMBEDDED.manual;

    if (
        embedded &&
        Array.isArray(embedded.segments)
    ) {
        pathSegments = embedded.segments.map(s => ({ ...s }));

        if (
            typeof embedded.color === 'string' &&
            /^#[0-9a-f]{6}$/i.test(embedded.color)
        ) {
            pathColor = embedded.color;
        }

        return;
    }

    try {
        const saved = JSON.parse(
            localStorage.getItem(PATH_STORAGE_KEY) || '[]'
        );

        if (!Array.isArray(saved)) return;

        pathSegments = saved.filter(seg =>
            Number.isFinite(seg.x1) &&
            Number.isFinite(seg.y1) &&
            Number.isFinite(seg.x2) &&
            Number.isFinite(seg.y2) &&
            typeof seg.color === 'string'
        );

    } catch (error) {
        console.warn(
            'Impossible de charger les tracés :',
            error
        );

        pathSegments = [];
    }
}

loadManualPath();

$('pathColor').value = pathColor;


// ------------------------------------------------------
// ÉTAT EXPORTABLE

window.__ETB_GET_MANUAL_TRACE__ = () => ({
    segments: pathSegments.map(s => ({ ...s })),
    color: pathColor
});


// ------------------------------------------------------
// INTERFACE

function updatePathUI() {
    $('pathToggle').textContent = pathDrawing
        ? 'Terminer (Échap)'
        : 'Tracer (T)';

    $('pathToggle').classList.toggle(
        'active',
        pathDrawing
    );

    $('pathFinish').disabled =
        !pathDrawing || pathPoints.length < 2;

    $('pathUndo').disabled =
        !pathDrawing || pathPoints.length === 0;

    $('pathDelete').disabled =
        selectedPathSegment < 0;

    if (pathDrawing) {
        $('pathHelp').textContent =
            'T ajoute un segment · clic gauche ajoute un point · Échap termine.';

    } else if (selectedPathSegment >= 0) {
        $('pathHelp').textContent =
            'Segment ' +
            (selectedPathSegment + 1) +
            ' sélectionné : modifie sa couleur.';

    } else {
        $('pathHelp').textContent =
            'T pour commencer · sélectionne un segment pour le recolorer.';
    }
}


// ------------------------------------------------------
// DÉMARRAGE ET FIN DU TRACÉ

function startPathDrawing() {
    pathDrawing = true;
    pathPoints = [];
    pathDraftStart = pathSegments.length;

    selectedPathSegment = -1;

    // Ne pas dessiner simultanément un itinéraire.
    routeSelecting = false;

    updatePathUI();
    routeUpdateUI();

    draw();
}


function finishPathDrawing() {
    if (!pathDrawing) return;

    pathDrawing = false;
    pathPoints = [];
    pathDraftStart = -1;

    saveManualPath();

    updatePathUI();
    draw();
}


// Annule uniquement le tracé en cours.

function cancelPathDrawing() {
    if (!pathDrawing) return;

    if (pathDraftStart >= 0) {
        pathSegments.splice(pathDraftStart);
    }

    pathDrawing = false;
    pathPoints = [];
    pathDraftStart = -1;

    saveManualPath();

    updatePathUI();
    draw();
}


// ------------------------------------------------------
// AJOUT DES POINTS

function addPathPointAtClient(clientX, clientY) {
    const point = canvasToWorld(clientX, clientY);

    if (pathPoints.length > 0) {
        const previous =
            pathPoints[pathPoints.length - 1];

        if (
            Math.hypot(
                point.x - previous.x,
                point.y - previous.y
            ) < 1
        ) {
            return;
        }

        pathSegments.push({
            x1: previous.x,
            y1: previous.y,
            x2: point.x,
            y2: point.y,
            color: pathColor,
            width: 5
        });
    }

    pathPoints.push(point);

    selectedPathSegment = -1;

    saveManualPath();

    updatePathUI();
    draw();
}


function addPathPoint(event) {
    addPathPointAtClient(
        event.clientX,
        event.clientY
    );
}


// ------------------------------------------------------
// ANNULER UN POINT

function undoPathPoint() {
    if (!pathDrawing || pathPoints.length === 0) return;

    if (pathPoints.length >= 2) {
        pathSegments.pop();
    }

    pathPoints.pop();

    selectedPathSegment = -1;

    saveManualPath();

    updatePathUI();
    draw();
}


// ------------------------------------------------------
// SÉLECTION DES SEGMENTS

function pointSegmentDistance(
    px, py, ax, ay, bx, by
) {
    const dx = bx - ax;
    const dy = by - ay;

    const lengthSquared = dx*dx + dy*dy;

    if (lengthSquared === 0) {
        return Math.hypot(px - ax, py - ay);
    }

    let t = (
        (px - ax)*dx +
        (py - ay)*dy
    ) / lengthSquared;

    t = Math.max(0, Math.min(1, t));

    const nearestX = ax + t*dx;
    const nearestY = ay + t*dy;

    return Math.hypot(
        px - nearestX,
        py - nearestY
    );
}


function findPathSegment(mx, my) {
    let best = -1;
    let bestDistance = 10;

    for (
        let i = pathSegments.length - 1;
        i >= 0;
        i--
    ) {
        const seg = pathSegments[i];

        const a = P(seg.x1, seg.y1);
        const b = P(seg.x2, seg.y2);

        const distance = pointSegmentDistance(
            mx, my,
            a[0], a[1],
            b[0], b[1]
        );

        if (distance < bestDistance) {
            bestDistance = distance;
            best = i;
        }
    }

    return best;
}


function selectPathSegment(index) {
    selectedPathSegment = index;

    if (index >= 0) {
        pathColor = pathSegments[index].color;

        $('pathColor').value = pathColor;

        window.dispatchEvent(
            new CustomEvent('etb-trace-color-change', {
                detail: {
                    color: pathColor,
                    source: 'manual'
                }
            })
        );
    }

    updatePathUI();
    draw();
}


// ------------------------------------------------------
// COULEURS SYNCHRONISÉES

function changePathColor(color) {
    pathColor = color;

    if (
        selectedPathSegment >= 0 &&
        pathSegments[selectedPathSegment]
    ) {
        pathSegments[selectedPathSegment].color = color;
        saveManualPath();
    }

    window.dispatchEvent(
        new CustomEvent('etb-trace-color-change', {
            detail: {
                color: color,
                source: 'manual'
            }
        })
    );

    updatePathUI();
    draw();
}


window.addEventListener(
    'etb-trace-color-change',
    event => {
        if (event.detail?.source !== 'route') return;

        const color = event.detail.color;

        pathColor = color;

        $('pathColor').value = color;

        if (
            selectedPathSegment >= 0 &&
            pathSegments[selectedPathSegment]
        ) {
            pathSegments[selectedPathSegment].color = color;
            saveManualPath();
        }

        updatePathUI();
        draw();
    }
);


$('pathColor').addEventListener('input', event => {
    changePathColor(event.target.value);
});


// ------------------------------------------------------
// SUPPRESSION

function deleteSelectedPathSegment() {
    if (selectedPathSegment < 0) return;

    pathSegments.splice(selectedPathSegment, 1);

    selectedPathSegment = -1;

    saveManualPath();

    updatePathUI();
    draw();
}


function clearManualPath() {
    if (pathSegments.length === 0) return;

    if (!confirm('Effacer tous les tracés manuels ?')) {
        return;
    }

    pathSegments = [];
    pathPoints = [];

    pathDrawing = false;
    selectedPathSegment = -1;
    pathDraftStart = -1;

    saveManualPath();

    updatePathUI();
    draw();
}


// ------------------------------------------------------
// EXPORT JSON DU TRACÉ

function exportManualPath() {
    const data = {
        title: document.title,
        segments: pathSegments
    };

    const blob = new Blob(
        [JSON.stringify(data, null, 2)],
        { type: 'application/json' }
    );

    const url = URL.createObjectURL(blob);

    const a = document.createElement('a');

    a.href = url;
    a.download = 'carte_traces.json';

    a.click();

    URL.revokeObjectURL(url);
}


// ------------------------------------------------------
// DESSIN DES TRACÉS MANUELS

function drawManualPath() {
    cx.save();

    cx.lineCap = 'round';
    cx.lineJoin = 'round';

    pathSegments.forEach((seg, index) => {
        const a = P(seg.x1, seg.y1);
        const b = P(seg.x2, seg.y2);

        if (index === selectedPathSegment) {
            cx.strokeStyle = '#ffffff';
            cx.lineWidth = 11;

            cx.beginPath();
            cx.moveTo(a[0], a[1]);
            cx.lineTo(b[0], b[1]);
            cx.stroke();
        }

        cx.strokeStyle = seg.color;
        cx.lineWidth = seg.width || 5;

        cx.beginPath();
        cx.moveTo(a[0], a[1]);
        cx.lineTo(b[0], b[1]);
        cx.stroke();

        cx.fillStyle = seg.color;

        cx.beginPath();
        cx.arc(a[0], a[1], 3, 0, Math.PI*2);
        cx.fill();
    });

    if (pathDrawing && pathPoints.length > 0) {
        const p = pathPoints[pathPoints.length - 1];
        const screen = P(p.x, p.y);

        cx.fillStyle = pathColor;

        cx.beginPath();
        cx.arc(
            screen[0],
            screen[1],
            6,
            0,
            Math.PI*2
        );

        cx.fill();

        cx.strokeStyle = '#ffffff';
        cx.lineWidth = 2;
        cx.stroke();
    }

    cx.restore();
}


// ======================================================
// RESTE DU MOTEUR DE CARTE
// ======================================================

const lay = z => {
    let b = 0;

    for (let i = 0; i < LZ.length; i++) {
        if (LZ[i] <= z + 50) b = i;
    }

    return b;
};


const pts = [...L, ...R, ...D].map(a => [a[0], a[1]])
    .concat(POL.map(a => [a[2][0], a[2][1]]));

const q = (a, p) =>
    a[Math.min(a.length - 1, Math.floor(a.length*p))];

const XS = pts.map(p => p[0]).sort((a,b) => a-b);
const YS = pts.map(p => p[1]).sort((a,b) => a-b);

const bx0 = XS.length ? q(XS, .005) : -1000;
const bx1 = XS.length ? q(XS, .995) : 1000;
const by0 = YS.length ? q(YS, .005) : -1000;
const by1 = YS.length ? q(YS, .995) : 1000;


$('ly').innerHTML =
    '<option value="-1">Tous</option>' +
    LBL.map((t,i) =>
        '<option value="' + i + '">' + t + '</option>'
    ).join('');

$('ly').value = layer;


if (!LIVE) {
    document.querySelectorAll('.lv').forEach(e => e.hidden = true);

    $('pn').hidden = true;
    $('st').hidden = true;
}


// ------------------------------------------------------
// PANNEAU DES CATÉGORIES

const esc = t =>
    String(t).replace(/[&<>"]/g, c => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;'
    }[c]));


function buildCats(filter) {
    const f = (filter || '').toLowerCase();

    let html =
        '<input type="search" id="sq" placeholder="Filtrer…" value="' +
        esc(filter || '') +
        '">' +
        '<div style="display:flex;gap:6px;margin-bottom:6px">' +
        '<button id="ca">Tout</button>' +
        '<button id="cn">Aucun</button>' +
        '<button id="cd">Défaut</button>' +
        '</div>';

    GR.forEach((g, gi) => {
        const ids = CATS.map((c, i) => i).filter(i =>
            CATS[i].g == gi &&
            (
                !f ||
                CATS[i].n.toLowerCase().includes(f) ||
                g.toLowerCase().includes(f)
            )
        );

        if (!ids.length) return;

        const all = ids.every(i => ON[i]);
        const none = ids.every(i => !ON[i]);

        html +=
            '<details' +
            (f || gi < __OPEN__ ? ' open' : '') +
            '><summary>' +
            '<input type="checkbox" data-g="' + gi + '"' +
            (all ? ' checked' : '') +
            ' data-ind="' + (!all && !none ? 1 : 0) +
            '"> ' + esc(g) +
            '<span class="n" style="margin-left:auto;opacity:.6;font-size:11px">' +
            ids.reduce((a,i) => a + CATS[i].cnt, 0) +
            '</span></summary>' +

            ids.map(i =>
                '<label class="row">' +
                '<input type="checkbox" data-c="' + i + '"' +
                (ON[i] ? ' checked' : '') +
                '><span class="sw" style="background:' +
                CATS[i].c +
                '"></span><span class="nm" title="' +
                esc(CATS[i].n) +
                '">' +
                esc(CATS[i].n) +
                '</span><span class="n">' +
                CATS[i].cnt +
                '</span></label>'
            ).join('') +

            '</details>';
    });

    $('cats').innerHTML = html;

    $('cats').querySelectorAll('input[data-ind="1"]').forEach(e => {
        e.indeterminate = true;
    });

    $('sq').oninput = e => {
        const value = e.target.value;

        buildCats(value);

        $('sq').focus();
        $('sq').setSelectionRange(1e6, 1e6);
    };

    $('ca').onclick = () => {
        ON = ON.map(_ => 1);
        buildCats($('sq').value);
        draw();
    };

    $('cn').onclick = () => {
        ON = ON.map(_ => 0);
        buildCats($('sq').value);
        draw();
    };

    $('cd').onclick = () => {
        ON = CATS.map(c => c.on);
        buildCats($('sq').value);
        draw();
    };

    $('cats').querySelectorAll('input[data-c]').forEach(e => {
        e.onchange = () => {
            ON[+e.dataset.c] = e.checked ? 1 : 0;

            buildCats($('sq').value);
            draw();
        };
    });

    $('cats').querySelectorAll('input[data-g]').forEach(e => {
        e.onchange = () => {
            CATS.forEach((c, i) => {
                if (c.g == +e.dataset.g) {
                    ON[i] = e.checked ? 1 : 0;
                }
            });

            buildCats($('sq').value);
            draw();
        };
    });
}


buildCats('');


// ------------------------------------------------------
// DESSIN DE LA CARTE

function fit() {
    const r = cv.getBoundingClientRect();
    const k = devicePixelRatio || 1;

    cv.width = r.width*k;
    cv.height = r.height*k;

    w = r.width;
    h = r.height;

    s = Math.min(
        w/(bx1-bx0+1500),
        h/(by1-by0+1500)
    );

    ox = w/2 - s*(bx0+bx1)/2;
    oy = h/2 - fy()*s*(by0+by1)/2;

    draw();
}


function center() {
    if (!pl) return;

    ox = w/2 - pl.x*s;
    oy = h/2 - fy()*pl.y*s;
}


function draw() {
    const k = devicePixelRatio || 1;

    cx.setTransform(k, 0, 0, k, 0, 0);
    cx.clearRect(0, 0, w, h);

    const st =
        [500, 1000, 2000, 5000, 10000].find(v => v*s >= 70)
        || 10000;

    cx.strokeStyle = '#26313d';
    cx.lineWidth = 1;
    cx.fillStyle = '#6b7785';
    cx.font = '11px sans-serif';

    for (
        let g = Math.floor(bx0/st)*st-st;
        g <= bx1+st;
        g += st
    ) {
        const [a] = P(g, 0);

        cx.beginPath();
        cx.moveTo(a, 0);
        cx.lineTo(a, h);
        cx.stroke();

        cx.fillText('X ' + g, a+2, 12);
    }

    for (
        let g = Math.floor(by0/st)*st-st;
        g <= by1+st;
        g += st
    ) {
        const [,b] = P(0, g);

        cx.beginPath();
        cx.moveTo(0, b);
        cx.lineTo(w, b);
        cx.stroke();

        cx.fillText('Y ' + g, 2, b-2);
    }

    // Tuiles

    for (const [x,y,l,fw,fh,an,c] of R) {
        if (!ON[c] || !vis(l)) continue;

        const [px,py] = P(x,y);

        cx.save();
        cx.translate(px,py);
        cx.rotate(fy()*an*Math.PI/180);

        cx.fillStyle = CATS[c].c + '22';
        cx.fillRect(
            -fw*s/2,
            -fh*s/2,
            fw*s,
            fh*s
        );

        cx.restore();
    }

    // Murs : contours de collision exacts

    for (const [l,c,pp] of POL) {
        if (!ON[c] || !vis(l)) continue;

        cx.beginPath();

        for (let i = 0; i < pp.length; i += 2) {
            const [qx,qy] = P(pp[i], pp[i+1]);

            if (i) cx.lineTo(qx, qy);
            else cx.moveTo(qx, qy);
        }

        cx.closePath();

        cx.fillStyle = CATS[c].c;
        cx.fill();

        cx.strokeStyle = CATS[c].c;
        cx.lineWidth = 1;
        cx.stroke();
    }

    // Segments

    cx.lineCap = 'butt';

    for (const [x,y,an,ln,l,c] of L) {
        if (!ON[c] || !vis(l)) continue;

        const [px,py] = P(x,y);

        const r = an*Math.PI/180;
        const dx = Math.cos(r)*ln/2*s;
        const dy = fy()*Math.sin(r)*ln/2*s;

        cx.strokeStyle = CATS[c].c;
        cx.lineWidth = Math.max(1.2, CATS[c].lw*s);

        cx.beginPath();
        cx.moveTo(px-dx, py-dy);
        cx.lineTo(px+dx, py+dy);
        cx.stroke();
    }

    // Points

    for (const [x,y,l,c] of D) {
        if (!ON[c] || !vis(l)) continue;

        const [px,py] = P(x,y);

        cx.fillStyle = CATS[c].c;
        cx.fillRect(px-1.5, py-1.5, 3, 3);
    }

    // Volumes

    if ($('vo').checked) {
        for (const [x,y,l,c,t,a,b,an] of V) {
            if (!ON[c] || !vis(l)) continue;

            const [px,py] = P(x,y);

            cx.strokeStyle = CATS[c].c;
            cx.fillStyle = CATS[c].c + '18';
            cx.lineWidth = 1.2;

            if (t == 'c') {
                cx.beginPath();
                cx.arc(px, py, a*s, 0, 7);
                cx.fill();
                cx.stroke();
            } else {
                cx.save();

                cx.translate(px,py);
                cx.rotate(fy()*an*Math.PI/180);

                cx.fillRect(
                    -a*s/2,
                    -b*s/2,
                    a*s,
                    b*s
                );

                cx.strokeRect(
                    -a*s/2,
                    -b*s/2,
                    a*s,
                    b*s
                );

                cx.restore();
            }
        }
    }

    // Objets

    for (const [x,y,l,c] of K) {
        if (!ON[c] || !vis(l)) continue;

        const [px,py] = P(x,y);
        const d = CATS[c];

        cx.fillStyle = cx.strokeStyle = d.c;

        if (d.s == 'd') {
            cx.beginPath();
            cx.moveTo(px,py-6);
            cx.lineTo(px+6,py);
            cx.lineTo(px,py+6);
            cx.lineTo(px-6,py);
            cx.closePath();
            cx.fill();
        }

        else if (d.s == 's') {
            cx.fillRect(px-4,py-4,8,8);
        }

        else if (d.s == 'r') {
            cx.lineWidth = 2;
            cx.beginPath();
            cx.arc(px,py,7,0,7);
            cx.stroke();
        }

        else {
            cx.beginPath();
            cx.arc(px,py,3,0,7);
            cx.fill();
        }
    }

    // Trace du joueur

    if (LIVE && $('tr').checked && tr.length > 1) {
        cx.strokeStyle = '#35b7ff88';
        cx.lineWidth = 2;

        cx.beginPath();

        tr.forEach((p,i) => {
            const [px,py] = P(p[0], p[1]);

            i
                ? cx.lineTo(px,py)
                : cx.moveTo(px,py);
        });

        cx.stroke();
    }

    // Tracé manuel

    drawManualPath();

    // Joueur

    if (pl) {
        const [px,py] = P(pl.x,pl.y);

        const r = pl.yaw*Math.PI/180;

        const dx = Math.cos(r);
        const dy = fy()*Math.sin(r);

        cx.strokeStyle = '#35b7ff99';
        cx.lineWidth = 3;

        cx.beginPath();
        cx.arc(px,py,20,0,7);
        cx.stroke();

        cx.fillStyle = '#35b7ff';
        cx.strokeStyle = '#fff';
        cx.lineWidth = 2;

        cx.beginPath();

        cx.moveTo(px+dx*13,py+dy*13);
        cx.lineTo(px-dx*8-dy*8,py-dy*8+dx*8);
        cx.lineTo(px-dx*3,py-dy*3);
        cx.lineTo(px-dx*8+dy*8,py-dy*8-dx*8);

        cx.closePath();
        cx.fill();
        cx.stroke();
    }
}


// ------------------------------------------------------
// INFOS AU SURVOL

function nearest(mx,my) {
    let best = null;
    let bd = 14;

    const chk = (x,y,l,c) => {
        if (!ON[c] || !vis(l)) return;

        const [px,py] = P(x,y);

        const d = Math.hypot(px-mx,py-my);

        if (d < bd) {
            bd = d;
            best =
                CATS[c].n +
                '  (' +
                Math.round(x) +
                ', ' +
                Math.round(y) +
                ')';
        }
    };

    for (const a of K) chk(a[0],a[1],a[2],a[3]);
    for (const a of D) chk(a[0],a[1],a[2],a[3]);
    for (const a of L) chk(a[0],a[1],a[4],a[5]);
    for (const a of V) chk(a[0],a[1],a[2],a[3]);
    for (const a of POL) chk(a[2][0],a[2][1],a[0],a[1]);

    return best;
}


// ------------------------------------------------------
// POSITION LIVE

function setPos(p) {
    pl = p;

    if ($('au').checked) {
        layer = lay((p.z||0)-ZOFF);
        $('ly').value = layer;
    }

    const l = tr[tr.length-1];

    if (
        !l ||
        Math.hypot(l[0]-p.x,l[1]-p.y) > 40
    ) {
        tr.push([p.x,p.y]);

        if (tr.length > 2000) {
            tr.shift();
        }
    }

    if ($('fo').checked) {
        center();
    }

    $('tx').textContent =
        `X ${p.x.toFixed(0)}  Y ${p.y.toFixed(0)}  ` +
        `Z ${(p.z||0).toFixed(0)}  yaw ${(p.yaw||0).toFixed(0)}°  ` +
        `calque ${layer}`;

    draw();
}


// ------------------------------------------------------
// PANNEAU LIVE

const f3 = v =>
    v
        ? `X ${v[0].toFixed(1)} · Y ${v[1].toFixed(1)} · Z ${v[2].toFixed(1)}`
        : 'indisponible';

const nu = (v,u) =>
    v == null
        ? 'indisponible'
        : v.toFixed(3) + (u||'');

let pnHtml = '';


function panel(p) {
    const d = (p && p.d) || {};

    let msg;
    let ok = false;

    if (!p) {
        msg =
            'Relais injoignable : lance etb_live_map.py puis ouvre http://127.0.0.1:8765/';
    }

    else {
        msg = p.status || '—';

        ok = !!p.ok && (p.age == null || p.age < 1.5);

        if (p.ok && p.age >= 1.5) {
            msg += ' (données périmées)';
        }
    }

    const rows = [
        ['Position réelle du personnage',f3(d.player_position)],
        ['Position de la caméra',f3(d.camera_location)],
        ['Pitch caméra',nu(d.pitch,'°')],
        ['Yaw caméra (orientation horizontale)',nu(d.yaw,'°')],
        ['Roll caméra',nu(d.roll,'°')],
        ['Champ de vision (FOV)',nu(d.fov)],
        ['Pawn',d.pawn||'indisponible'],
        ['RootComponent',d.root_component||'indisponible'],
        ['PlayerCameraManager',d.camera_manager||'indisponible'],
        ['Processus',d.pid ? `PID ${d.pid} · module ${d.module} · GWorld ${d.slot}` : '—'],
        ['Calque affiché',layer<0?'tous':LBL[layer]],
        ['Âge de la donnée',p && p.age != null ? p.age+' s' : '—']
    ];

    const html =
        '<h3>Données du joueur</h3>' +
        '<div style="color:' +
        (ok?'#5fd0a0':'#ffb86b') +
        '">' +
        esc(msg) +
        '</div>' +

        rows.map(([k,v]) =>
            '<div class="k">' +
            esc(k) +
            '</div><div class="v">' +
            esc(v) +
            '</div>'
        ).join('');

    if (html !== pnHtml) {
        pnHtml = html;
        $('pn').innerHTML = html;
    }
}


async function poll() {
    let p = null;

    try {
        const r = await fetch(SRC, { cache:'no-store' });

        if (r.ok) {
            p = await r.json();
        }
    } catch(e) {}

    if (
        p &&
        typeof p.x == 'number' &&
        (p.age === undefined || p.age < 1.5)
    ) {
        last = Date.now();

        setPos({
            x:p.x,
            y:p.y,
            z:p.z||0,
            yaw:p.yaw||0
        });
    }

    $('st').style.background =
        Date.now()-last < 1500
            ? '#5fd0a0'
            : '#e55';

    panel(p);

    setTimeout(poll, POLL);
}


// ======================================================
// CHEMIN LE PLUS COURT
// FONCTIONNEMENT DE CALCUL CONSERVÉ
// ======================================================

const ROUTE_CELL = 50;
const ROUTE_CLEARANCE = 40;
const ROUTE_CELL_FINE = 25;
const ROUTE_POLY_CLEARANCE = 28;
const ROUTE_MAX_CELLS = 1200000;
const ROUTE_MAX_TARGETS = 10;

const ROUTE_STORAGE_KEY =
    'etb-route-points:' +
    location.pathname +
    ':' +
    document.title;

let routeWaypoints = [];
let routePath = [];
let routeVisitOrder = [];
let routeStart = null;
let routeStartsFromPlayer = false;
let routeSelecting = false;
let routeColor = '#00d5ff';
let routeDistance = 0;


const routeStatus = message => {
    $('routeStatus').textContent = message;
};


const routePoint = (x,y) => ({
    x:Number(x),
    y:Number(y)
});


const routeDistance2D = (a,b) =>
    Math.hypot(a.x-b.x,a.y-b.y);


// ------------------------------------------------------
// SAUVEGARDE

function routeSave() {
    try {
        localStorage.setItem(
            ROUTE_STORAGE_KEY,
            JSON.stringify({
                points:routeWaypoints,
                color:routeColor
            })
        );
    } catch(e) {
        console.warn(
            'Sauvegarde du parcours impossible :',
            e
        );
    }
}


// ------------------------------------------------------
// CHARGEMENT

function routeLoad() {
    const embedded = EMBEDDED.route;

    if (
        embedded &&
        Array.isArray(embedded.waypoints)
    ) {
        routeWaypoints =
            embedded.waypoints.map(p => ({...p}));

        routePath =
            Array.isArray(embedded.path)
                ? embedded.path.map(p => ({...p}))
                : [];

        routeVisitOrder =
            Array.isArray(embedded.visitOrder)
                ? [...embedded.visitOrder]
                : [];

        routeStart =
            embedded.start
                ? {...embedded.start}
                : null;

        routeStartsFromPlayer = !!embedded.startsFromPlayer;
        routeDistance = Number(embedded.distance) || 0;

        if (
            typeof embedded.color === 'string' &&
            /^#[0-9a-f]{6}$/i.test(embedded.color)
        ) {
            routeColor = embedded.color;
        }

        $('routeColor').value = routeColor;

        return;
    }

    try {
        const saved = JSON.parse(
            localStorage.getItem(ROUTE_STORAGE_KEY) || 'null'
        );

        if (
            saved &&
            Array.isArray(saved.points)
        ) {
            routeWaypoints = saved.points.filter(p =>
                Number.isFinite(p.x) &&
                Number.isFinite(p.y)
            );

            if (
                typeof saved.color === 'string' &&
                /^#[0-9a-f]{6}$/i.test(saved.color)
            ) {
                routeColor = saved.color;
            }
        }

    } catch(e) {
        console.warn(
            'Chargement du parcours impossible :',
            e
        );
    }

    $('routeColor').value = routeColor;
}


// ------------------------------------------------------
// INTERFACE

function routeUpdateUI() {
    $('routeSelect').classList.toggle(
        'active',
        routeSelecting
    );

    $('routeSelect').textContent = routeSelecting
        ? 'Terminer les points'
        : 'Ajouter des points';

    $('routeUndo').disabled =
        routeWaypoints.length === 0;

    $('routeCalculate').disabled =
        routeWaypoints.length < (LIVE ? 1 : 2);

    routeStatus(
        routeSelecting
            ? `Clique sur la carte pour ajouter des destinations (${routeWaypoints.length}/${ROUTE_MAX_TARGETS}).`
            : `${routeWaypoints.length} point(s) sélectionné(s). ${
                routePath.length
                    ? `Trajet : ${(routeDistance/100).toFixed(1)} m.`
                    : 'Calcule le parcours.'
            }`
    );
}


// ------------------------------------------------------
// AJOUT D'UNE DESTINATION

function routeAddWaypoint(event) {
    if (routeWaypoints.length >= ROUTE_MAX_TARGETS) {
        routeStatus(
            `Limite de ${ROUTE_MAX_TARGETS} destinations atteinte.`
        );

        return;
    }

    const point = canvasToWorld(
        event.clientX,
        event.clientY
    );

    routeWaypoints.push(point);

    routePath = [];
    routeVisitOrder = [];
    routeDistance = 0;

    routeSave();
    routeUpdateUI();
    draw();
}


function routeToggleSelection() {
    routeSelecting = !routeSelecting;

    if (!routeSelecting) {
        routeStatus('Sélection des points terminée.');
    }

    if (routeSelecting && pathDrawing) {
        finishPathDrawing();
    }

    routeUpdateUI();
}


function routeUndo() {
    if (!routeWaypoints.length) return;

    routeWaypoints.pop();

    routePath = [];
    routeVisitOrder = [];
    routeDistance = 0;

    routeSave();
    routeUpdateUI();
    draw();
}


function routeClear() {
    if (!routeWaypoints.length && !routePath.length) return;

    routeWaypoints = [];
    routePath = [];
    routeVisitOrder = [];

    routeStart = null;
    routeStartsFromPlayer = false;
    routeDistance = 0;
    routeSelecting = false;

    routeSave();
    routeUpdateUI();
    draw();
}


// ------------------------------------------------------
// DISTANCE POINT / SEGMENT

function routePointSegmentDistance(
    px,py,ax,ay,bx,by
) {
    const dx = bx-ax;
    const dy = by-ay;

    const len2 = dx*dx + dy*dy;

    if (!len2) {
        return Math.hypot(px-ax,py-ay);
    }

    const t = Math.max(
        0,
        Math.min(
            1,
            ((px-ax)*dx+(py-ay)*dy)/len2
        )
    );

    return Math.hypot(
        px-(ax+t*dx),
        py-(ay+t*dy)
    );
}


// ------------------------------------------------------
// RÉCUPÉRATION DES MURS

function routeActiveLayer(points) {
    if (layer >= 0) return layer;

    if (LIVE && pl) return lay((pl.z||0)-ZOFF);

    // Mode « Tous » : calque dont les obstacles sont les plus proches des points choisis
    // (avant : toujours le calque 0 = le plus bas, d'où des murs ignorés).

    const blockedGroups = new Set(['Murs','Clôtures / barrières']);

    const n = Math.max(1, LZ.length);
    const score = new Array(n).fill(0);
    const total = new Array(n).fill(0);

    const near = (x,y) =>
        points.some(p => Math.hypot(p.x-x, p.y-y) <= 1500);

    for (const [x,y,an,ln,l,c] of L) {
        if (!blockedGroups.has(GR[CATS[c]?.g])) continue;

        total[l]++;
        if (near(x,y)) score[l]++;
    }

    for (const [l,c,pp] of POL) {
        if (!CATS[c]?.blk) continue;

        total[l]++;
        if (near(pp[0],pp[1])) score[l]++;
    }

    let best = 0;

    for (let i = 1; i < n; i++) {
        if (
            score[i] > score[best] ||
            (score[i] === score[best] && total[i] > total[best])
        ) {
            best = i;
        }
    }

    return best;
}


function routeGetWalls(points) {
    const activeLayer = routeActiveLayer(points || []);

    const blockedGroups = new Set([
        'Murs',
        'Clôtures / barrières'
    ]);

    const walls = [];

    for (const item of L) {
        const [
            x,y,angle,length,itemLayer,category
        ] = item;

        const groupName = GR[CATS[category]?.g];

        if (!blockedGroups.has(groupName)) continue;
        if (itemLayer !== activeLayer) continue;

        const radians = angle*Math.PI/180;

        const dx = Math.cos(radians)*length/2;
        const dy = Math.sin(radians)*length/2;

        const halfWidth = Math.max(
            10,
            (CATS[category]?.lw||0)/2
        );

        walls.push({
            ax:x-dx,
            ay:y-dy,
            bx:x+dx,
            by:y+dy,
            radius:halfWidth+ROUTE_CLEARANCE
        });
    }

    // Contours de collision exacts (meshes dont on connaît la collision)

    const polys = [];

    for (const [l,c,pp] of POL) {
        if (l !== activeLayer || !CATS[c]?.blk) continue;

        polys.push(pp);
    }

    return {walls,polys,activeLayer};
}


function routePointPolyDistance(px,py,pp) {
    const n = pp.length/2;

    let inside = false;

    for (let i = 0, j = n-1; i < n; j = i++) {
        const xi = pp[2*i],   yi = pp[2*i+1];
        const xj = pp[2*j],   yj = pp[2*j+1];

        if (
            (yi > py) !== (yj > py) &&
            px < (xj-xi)*(py-yi)/(yj-yi)+xi
        ) {
            inside = !inside;
        }
    }

    if (inside) return 0;

    let best = Infinity;

    for (let i = 0, j = n-1; i < n; j = i++) {
        best = Math.min(
            best,
            routePointSegmentDistance(
                px,py,
                pp[2*j],pp[2*j+1],
                pp[2*i],pp[2*i+1]
            )
        );
    }

    return best;
}


// ------------------------------------------------------
// CONSTRUCTION DE LA GRILLE

function routeBuildGrid(points) {
    const {walls,polys,activeLayer} = routeGetWalls(points);

    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;

    const include = p => {
        minX = Math.min(minX,p.x);
        minY = Math.min(minY,p.y);
        maxX = Math.max(maxX,p.x);
        maxY = Math.max(maxY,p.y);
    };

    points.forEach(include);

    walls.forEach(w => {
        include({x:w.ax,y:w.ay});
        include({x:w.bx,y:w.by});
    });

    polys.forEach(pp => {
        for (let i = 0; i < pp.length; i += 2) {
            include({x:pp[i],y:pp[i+1]});
        }
    });

    if (!Number.isFinite(minX)) {
        throw new Error(
            'Aucune coordonnée valide pour construire la carte de navigation.'
        );
    }

    const margin = 1000;

    minX -= margin;
    minY -= margin;
    maxX += margin;
    maxY += margin;

    const spanX = maxX-minX;
    const spanY = maxY-minY;

    // Grille plus fine quand on a des contours exacts : les portes
    // (~100 cm) doivent rester franchissables.

    let cell = polys.length ? ROUTE_CELL_FINE : ROUTE_CELL;

    while (
        Math.ceil(spanX/cell)*
        Math.ceil(spanY/cell) > ROUTE_MAX_CELLS &&
        cell < 250
    ) {
        cell += 5;
    }

    const width = Math.ceil(spanX/cell)+1;
    const height = Math.ceil(spanY/cell)+1;
    const size = width*height;

    if (size > ROUTE_MAX_CELLS*1.2) {
        throw new Error(
            'La carte est trop grande pour le calcul.'
        );
    }

    const blocked = new Uint8Array(size);

    const toCell = p => ({
        x:Math.max(
            0,
            Math.min(width-1,Math.floor((p.x-minX)/cell))
        ),
        y:Math.max(
            0,
            Math.min(height-1,Math.floor((p.y-minY)/cell))
        )
    });

    const toWorld = (x,y) => ({
        x:minX+(x+.5)*cell,
        y:minY+(y+.5)*cell
    });

    const index = (x,y) => y*width+x;

    for (const wall of walls) {
        const r = wall.radius+cell*.45;

        const x0 = Math.max(
            0,
            Math.floor(
                (Math.min(wall.ax,wall.bx)-r-minX)/cell
            )
        );

        const x1 = Math.min(
            width-1,
            Math.ceil(
                (Math.max(wall.ax,wall.bx)+r-minX)/cell
            )
        );

        const y0 = Math.max(
            0,
            Math.floor(
                (Math.min(wall.ay,wall.by)-r-minY)/cell
            )
        );

        const y1 = Math.min(
            height-1,
            Math.ceil(
                (Math.max(wall.ay,wall.by)+r-minY)/cell
            )
        );

        for (let gy=y0; gy<=y1; gy++) {
            for (let gx=x0; gx<=x1; gx++) {
                const p = toWorld(gx,gy);

                if (
                    routePointSegmentDistance(
                        p.x,p.y,
                        wall.ax,wall.ay,
                        wall.bx,wall.by
                    ) <= r
                ) {
                    blocked[index(gx,gy)] = 1;
                }
            }
        }
    }

    // Segments réels des murs (sert à empêcher un point de départ/arrivée d'être
    // « déplacé » de l'autre côté d'un mur)

    const wallSegs = [];

    for (const wall of walls) {
        wallSegs.push({id:-1,ax:wall.ax,ay:wall.ay,bx:wall.bx,by:wall.by});
    }

    polys.forEach((pp,id) => {
        const n = pp.length/2;

        for (let i = 0, j = n-1; i < n; j = i++) {
            wallSegs.push({
                id,
                ax:pp[2*j],ay:pp[2*j+1],
                bx:pp[2*i],by:pp[2*i+1]
            });
        }
    });

    const polyR = ROUTE_POLY_CLEARANCE+cell*.35;

    for (const pp of polys) {
        let ax0 = Infinity, ay0 = Infinity;
        let ax1 = -Infinity, ay1 = -Infinity;

        for (let i = 0; i < pp.length; i += 2) {
            ax0 = Math.min(ax0,pp[i]);
            ax1 = Math.max(ax1,pp[i]);
            ay0 = Math.min(ay0,pp[i+1]);
            ay1 = Math.max(ay1,pp[i+1]);
        }

        const x0 = Math.max(0,Math.floor((ax0-polyR-minX)/cell));
        const x1 = Math.min(width-1,Math.ceil((ax1+polyR-minX)/cell));
        const y0 = Math.max(0,Math.floor((ay0-polyR-minY)/cell));
        const y1 = Math.min(height-1,Math.ceil((ay1+polyR-minY)/cell));

        for (let gy=y0; gy<=y1; gy++) {
            for (let gx=x0; gx<=x1; gx++) {
                const p = toWorld(gx,gy);

                if (routePointPolyDistance(p.x,p.y,pp) <= polyR) {
                    blocked[index(gx,gy)] = 1;
                }
            }
        }
    }

    return {
        minX,minY,
        width,height,
        cell,size,
        blocked,
        toCell,
        toWorld,
        index,
        activeLayer,
        wallSegs,
        polys
    };
}


function routeSegCross(ax,ay,bx,by,cx,cy,dx,dy) {
    const o = (px,py,qx,qy,rx,ry) =>
        (qx-px)*(ry-py)-(qy-py)*(rx-px);

    const d1 = o(ax,ay,bx,by,cx,cy);
    const d2 = o(ax,ay,bx,by,dx,dy);
    const d3 = o(cx,cy,dx,dy,ax,ay);
    const d4 = o(cx,cy,dx,dy,bx,by);

    return (d1>0) !== (d2>0) && (d3>0) !== (d4>0);
}


// ------------------------------------------------------
// CELLULE LIBRE

function routeNearestFree(grid,point) {
    const c = grid.toCell(point);

    const valid = (x,y) =>
        x>=0 &&
        y>=0 &&
        x<grid.width &&
        y<grid.height &&
        !grid.blocked[grid.index(x,y)];

    if (valid(c.x,c.y)) {
        return grid.index(c.x,c.y);
    }

    // Le point est dans la zone de sécurité d'un mur : on cherche la case libre
    // la plus proche, mais sans jamais traverser un mur pour l'atteindre.
    // (Un point cliqué à l'intérieur d'un contour de mur peut en sortir librement.)

    const skip = new Set();

    (grid.polys || []).forEach((pp,id) => {
        if (routePointPolyDistance(point.x,point.y,pp) === 0) {
            skip.add(id);
        }
    });

    const crosses = (x,y) => {
        const lx0 = Math.min(point.x,x), lx1 = Math.max(point.x,x);
        const ly0 = Math.min(point.y,y), ly1 = Math.max(point.y,y);

        for (const s of grid.wallSegs || []) {
            if (skip.has(s.id)) continue;

            if (
                Math.max(s.ax,s.bx) < lx0 || Math.min(s.ax,s.bx) > lx1 ||
                Math.max(s.ay,s.by) < ly0 || Math.min(s.ay,s.by) > ly1
            ) {
                continue;
            }

            if (
                routeSegCross(
                    point.x,point.y,x,y,
                    s.ax,s.ay,s.bx,s.by
                )
            ) {
                return true;
            }
        }

        return false;
    };

    let fallback = -1;

    for (let radius=1; radius<=20; radius++) {
        const cand = [];

        for (let dy=-radius; dy<=radius; dy++) {
            for (let dx=-radius; dx<=radius; dx++) {
                if (
                    Math.max(Math.abs(dx),Math.abs(dy)) !== radius
                ) {
                    continue;
                }

                const x=c.x+dx;
                const y=c.y+dy;

                if (!valid(x,y)) continue;

                cand.push([dx*dx+dy*dy,x,y]);
            }
        }

        cand.sort((a,b) => a[0]-b[0]);

        // Repli : si le point touche un mur au point qu'aucun déplacement propre
        // n'existe, on garde la case libre la plus proche.
        if (fallback < 0 && cand.length) {
            fallback = grid.index(cand[0][1],cand[0][2]);
        }

        for (const [,x,y] of cand) {
            const wp = grid.toWorld(x,y);

            if (!crosses(wp.x,wp.y)) {
                return grid.index(x,y);
            }
        }
    }

    return fallback;
}


// ------------------------------------------------------
// FILE DE PRIORITÉ

class RouteMinHeap {
    constructor() {
        this.items=[];
    }

    push(node,score) {
        const a=this.items;
        let i=a.length;

        a.push({node,score});

        while (i>0) {
            const p=(i-1)>>1;

            if (a[p].score<=score) break;

            a[i]=a[p];
            i=p;
        }

        a[i]={node,score};
    }

    pop() {
        const a=this.items;

        if (!a.length) return null;

        const root=a[0];
        const last=a.pop();

        if (a.length) {
            let i=0;

            while (true) {
                const l=i*2+1;
                const r=l+1;

                if (l>=a.length) break;

                const c =
                    (r<a.length && a[r].score<a[l].score)
                        ? r
                        : l;

                if (a[c].score>=last.score) break;

                a[i]=a[c];
                i=c;
            }

            a[i]=last;
        }

        return root;
    }

    get length() {
        return this.items.length;
    }
}


// ------------------------------------------------------
// A* : CHEMIN ENTRE DEUX POINTS

function routeAStar(grid,startPoint,endPoint) {
    const start=routeNearestFree(grid,startPoint);
    const goal=routeNearestFree(grid,endPoint);

    if (start<0 || goal<0) return null;

    if (start===goal) {
        return [
            routePoint(startPoint.x,startPoint.y),
            routePoint(endPoint.x,endPoint.y)
        ];
    }

    const gScore=new Float32Array(grid.size);
    gScore.fill(Infinity);

    const parent=new Int32Array(grid.size);
    parent.fill(-1);

    const closed=new Uint8Array(grid.size);

    const heap=new RouteMinHeap();

    const sx=start%grid.width;
    const sy=Math.floor(start/grid.width);

    const gx=goal%grid.width;
    const gy=Math.floor(goal/grid.width);

    const heuristic=(x,y)=>{
        const dx=Math.abs(x-gx);
        const dy=Math.abs(y-gy);

        return (
            dx+dy+
            (Math.SQRT2-2)*Math.min(dx,dy)
        );
    };

    const neighbors=[
        [-1,-1,Math.SQRT2],
        [0,-1,1],
        [1,-1,Math.SQRT2],
        [-1,0,1],
        [1,0,1],
        [-1,1,Math.SQRT2],
        [0,1,1],
        [1,1,Math.SQRT2]
    ];

    gScore[start]=0;
    heap.push(start,heuristic(sx,sy));

    let found=false;
    let expanded=0;

    while (heap.length) {
        const item=heap.pop();
        const current=item.node;

        if (closed[current]) continue;

        if (current===goal) {
            found=true;
            break;
        }

        closed[current]=1;
        expanded++;

        if (expanded>grid.size) break;

        const x=current%grid.width;
        const y=Math.floor(current/grid.width);

        for (const [dx,dy,cost] of neighbors) {
            const nx=x+dx;
            const ny=y+dy;

            if (
                nx<0 ||
                ny<0 ||
                nx>=grid.width ||
                ny>=grid.height
            ) {
                continue;
            }

            const ni=grid.index(nx,ny);

            if (grid.blocked[ni] || closed[ni]) continue;

            if (
                dx!==0 &&
                dy!==0 &&
                (
                    grid.blocked[grid.index(x+dx,y)] ||
                    grid.blocked[grid.index(x,y+dy)]
                )
            ) {
                continue;
            }

            const tentative=gScore[current]+cost;

            if (tentative<gScore[ni]) {
                gScore[ni]=tentative;
                parent[ni]=current;

                heap.push(
                    ni,
                    tentative+heuristic(nx,ny)
                );
            }
        }
    }

    if (!found) return null;

    const reversed=[];
    let cursor=goal;

    while (cursor!==-1) {
        reversed.push(cursor);

        if (cursor===start) break;

        cursor=parent[cursor];
    }

    if (reversed[reversed.length-1]!==start) {
        return null;
    }

    reversed.reverse();

    const raw=[
        routePoint(startPoint.x,startPoint.y)
    ];

    for (let i=0; i<reversed.length; i++) {
        const node=reversed[i];

        const x=node%grid.width;
        const y=Math.floor(node/grid.width);

        const p=grid.toWorld(x,y);

        if (
            routeDistance2D(
                raw[raw.length-1],
                p
            ) > grid.cell*.25
        ) {
            raw.push(p);
        }
    }

    raw.push(
        routePoint(endPoint.x,endPoint.y)
    );

    const clearLine=(a,b)=>{
        const distance=routeDistance2D(a,b);

        const steps=Math.max(
            1,
            Math.ceil(distance/(grid.cell*.35))
        );

        for (let i=0;i<=steps;i++) {
            const t=i/steps;

            const p={
                x:a.x+(b.x-a.x)*t,
                y:a.y+(b.y-a.y)*t
            };

            const c=grid.toCell(p);

            if (grid.blocked[grid.index(c.x,c.y)]) {
                return false;
            }
        }

        return true;
    };

    const smooth=[raw[0]];
    let anchor=0;

    while (anchor<raw.length-1) {
        let next=raw.length-1;

        while (
            next>anchor+1 &&
            !clearLine(raw[anchor],raw[next])
        ) {
            next--;
        }

        smooth.push(raw[next]);
        anchor=next;
    }

    return smooth;
}


// ------------------------------------------------------
// LONGUEUR ET OPTIMISATION DE L'ORDRE

function routePolylineLength(points) {
    let total=0;

    for (let i=1;i<points.length;i++) {
        total+=routeDistance2D(
            points[i-1],
            points[i]
        );
    }

    return total;
}


function routeOptimizeOrder(distanceMatrix) {
    const n=distanceMatrix.length-1;

    if (n<=0) return [];

    const states=1<<n;

    const dp=Array.from(
        {length:states},
        ()=>{
            const row=new Float64Array(n);
            row.fill(Infinity);
            return row;
        }
    );

    const prev=Array.from(
        {length:states},
        ()=>{
            const row=new Int16Array(n);
            row.fill(-1);
            return row;
        }
    );

    for (let j=0;j<n;j++) {
        dp[1<<j][j]=distanceMatrix[0][j+1];
    }

    for (let mask=1;mask<states;mask++) {
        for (let last=0;last<n;last++) {
            const current=dp[mask][last];

            if (!Number.isFinite(current)) continue;

            for (let next=0;next<n;next++) {
                if (mask&(1<<next)) continue;

                const nextMask=mask|(1<<next);

                const candidate =
                    current+
                    distanceMatrix[last+1][next+1];

                if (candidate<dp[nextMask][next]) {
                    dp[nextMask][next]=candidate;
                    prev[nextMask][next]=last;
                }
            }
        }
    }

    const full=states-1;

    let last=-1;
    let best=Infinity;

    for (let j=0;j<n;j++) {
        if (dp[full][j]<best) {
            best=dp[full][j];
            last=j;
        }
    }

    if (last<0 || !Number.isFinite(best)) {
        return null;
    }

    const order=[];
    let mask=full;

    while (last>=0) {
        order.push(last);

        const previous=prev[mask][last];

        mask^=1<<last;
        last=previous;
    }

    return order.reverse();
}


// ------------------------------------------------------
// CALCUL DU TRAJET COMPLET

function routeCalculate() {
    routeSelecting=false;

    if (routeWaypoints.length===0) {
        routeStatus('Ajoute au moins un point de destination.');
        routeUpdateUI();
        return;
    }

    let start;
    let targets;

    if (LIVE && pl) {
        start=routePoint(pl.x,pl.y);
        targets=routeWaypoints.slice();
        routeStartsFromPlayer=true;

    } else {
        if (routeWaypoints.length<2) {
            routeStatus(
                'Sur une carte statique, choisis au moins deux points : départ puis destinations.'
            );

            routeUpdateUI();
            return;
        }

        start=routeWaypoints[0];
        targets=routeWaypoints.slice(1);
        routeStartsFromPlayer=false;
    }

    if (targets.length>ROUTE_MAX_TARGETS) {
        routeStatus(
            `Maximum ${ROUTE_MAX_TARGETS} destinations pour le calcul exact.`
        );

        routeUpdateUI();
        return;
    }

    routeStatus(
        'Construction du parcours et calcul des chemins…'
    );

    $('routeCalculate').disabled=true;

    setTimeout(()=>{
        try {
            const allPoints=[start,...targets];

            const grid=routeBuildGrid(allPoints);

            const count=allPoints.length;

            const paths=Array.from(
                {length:count},
                ()=>Array(count).fill(null)
            );

            const distances=Array.from(
                {length:count},
                ()=>Array(count).fill(Infinity)
            );

            for (let i=0;i<count;i++) {
                distances[i][i]=0;
            }

            for (let i=0;i<count;i++) {
                for (let j=i+1;j<count;j++) {
                    const path=routeAStar(
                        grid,
                        allPoints[i],
                        allPoints[j]
                    );

                    if (!path) {
                        throw new Error(
                            `Aucun passage trouvé entre le point ${i===0?'de départ':i} et le point ${j}. Vérifie les points et les murs de ce calque.`
                        );
                    }

                    const distance=routePolylineLength(path);

                    paths[i][j]=path;
                    paths[j][i]=path.slice().reverse();

                    distances[i][j]=distance;
                    distances[j][i]=distance;
                }
            }

            const order=routeOptimizeOrder(distances);

            if (!order) {
                throw new Error(
                    'Impossible de déterminer un ordre de visite complet.'
                );
            }

            const combined=[];
            let current=0;

            for (const targetIndex of order) {
                const segment=paths[current][targetIndex+1];

                if (!segment) {
                    throw new Error(
                        'Un segment du parcours est manquant.'
                    );
                }

                for (let p=0;p<segment.length;p++) {
                    if (combined.length && p===0) continue;
                    combined.push(segment[p]);
                }

                current=targetIndex+1;
            }

            routePath=combined;

            routeVisitOrder=order.map(i=>
                routeStartsFromPlayer ? i : i+1
            );

            routeStart=start;

            routeDistance=routePolylineLength(routePath);

            routeSave();

            routeStatus(
                `Trajet calculé : ${(routeDistance/100).toFixed(1)} m · ${targets.length} destination(s) · calque ${LBL[grid.activeLayer] || (grid.activeLayer+1)} · grille ${grid.cell} cm.`
            );

            routeUpdateUI();
            draw();

        } catch(error) {
            routePath=[];
            routeVisitOrder=[];
            routeDistance=0;

            routeStatus(
                error.message ||
                'Erreur pendant le calcul du trajet.'
            );

            console.error(error);

            routeUpdateUI();
            draw();

        } finally {
            $('routeCalculate').disabled =
                routeWaypoints.length<(LIVE?1:2);
        }
    },20);
}


// ------------------------------------------------------
// DESSIN DU TRAJET

function routeDrawOverlay() {
    if (!routePath.length && !routeWaypoints.length) {
        return;
    }

    cx.save();

    if (routePath.length>1) {
        cx.lineCap='round';
        cx.lineJoin='round';

        cx.beginPath();

        routePath.forEach((p,i)=>{
            const q=P(p.x,p.y);

            if (i===0) {
                cx.moveTo(q[0],q[1]);
            } else {
                cx.lineTo(q[0],q[1]);
            }
        });

        cx.strokeStyle='#101820';
        cx.lineWidth=10;
        cx.stroke();

        cx.strokeStyle=routeColor;
        cx.lineWidth=5;
        cx.stroke();
    }

    if (routeStart && routeStartsFromPlayer) {
        const p=P(routeStart.x,routeStart.y);

        cx.fillStyle='#16a34a';
        cx.strokeStyle='#fff';
        cx.lineWidth=2;

        cx.beginPath();
        cx.arc(p[0],p[1],8,0,Math.PI*2);
        cx.fill();
        cx.stroke();

        cx.fillStyle='#fff';
        cx.font='bold 10px sans-serif';
        cx.textAlign='center';
        cx.textBaseline='middle';
        cx.fillText('D',p[0],p[1]);
    }

    routeWaypoints.forEach((point,index)=>{
        const p=P(point.x,point.y);

        let order=index+1;

        if (routeVisitOrder.length) {
            const found=routeVisitOrder.indexOf(index);

            if (found>=0) {
                order=found+1;
            } else if (!routeStartsFromPlayer && index===0) {
                order='D';
            }
        }

        cx.fillStyle=routeColor;
        cx.strokeStyle='#fff';
        cx.lineWidth=2;

        cx.beginPath();
        cx.arc(p[0],p[1],9,0,Math.PI*2);
        cx.fill();
        cx.stroke();

        cx.fillStyle='#101820';
        cx.font='bold 10px sans-serif';
        cx.textAlign='center';
        cx.textBaseline='middle';
        cx.fillText(String(order),p[0],p[1]);
    });

    cx.restore();
}


// ------------------------------------------------------
// EXPORT JSON DU CHEMIN

function routeExport() {
    const payload = {
        mapTitle:document.title,
        start:routeStart,
        startsFromPlayer:routeStartsFromPlayer,
        waypoints:routeWaypoints,
        visitOrder:routeVisitOrder,
        path:routePath,
        distanceUE:routeDistance,
        distanceMeters:routeDistance/100,
        obstacleGroups:[
            'Murs',
            'Clôtures / barrières'
        ],
        gridCellCm:ROUTE_CELL
    };

    const blob=new Blob(
        [JSON.stringify(payload,null,2)],
        {type:'application/json'}
    );

    const url=URL.createObjectURL(blob);

    const a=document.createElement('a');

    a.href=url;
    a.download='itineraire_carte.json';

    a.click();

    URL.revokeObjectURL(url);
}


// ------------------------------------------------------
// ÉTAT DU CHEMIN EXPORTABLE

routeLoad();
routeUpdateUI();

window.__ETB_GET_ROUTE_STATE__ = () => ({
    waypoints:routeWaypoints.map(p=>({...p})),
    path:routePath.map(p=>({...p})),
    visitOrder:[...routeVisitOrder],
    start:routeStart ? {...routeStart} : null,
    startsFromPlayer:routeStartsFromPlayer,
    distance:routeDistance,
    color:routeColor
});


// ------------------------------------------------------
// SYNCHRONISATION DES COULEURS

window.addEventListener(
    'etb-trace-color-change',
    event => {
        if (event.detail?.source!=='manual') return;

        routeColor=event.detail.color;

        $('routeColor').value=routeColor;

        routeSave();
        draw();
    }
);


$('routeColor').addEventListener('input',event=>{
    routeColor=event.target.value;

    routeSave();

    window.dispatchEvent(
        new CustomEvent('etb-trace-color-change',{
            detail:{
                color:routeColor,
                source:'route'
            }
        })
    );

    draw();
});


// ======================================================
// EXPORT HTML COMPLET
// ======================================================

function exportCompleteHTML() {
    const manualState =
        window.__ETB_GET_MANUAL_TRACE__?.() || {
            segments:[],
            color:'#ff5252'
        };

    const routeState =
        window.__ETB_GET_ROUTE_STATE__?.() || {
            waypoints:[],
            path:[],
            visitOrder:[],
            start:null,
            startsFromPlayer:false,
            distance:0,
            color:'#00d5ff'
        };

    const playerState=pl
        ? {
            x:pl.x,
            y:pl.y,
            z:pl.z,
            yaw:pl.yaw
        }
        : null;

    const viewCenter={
        x:(w/2-ox)/s,
        y:fy()*(h/2-oy)/s
    };

    const exportState={
        version:1,
        manual:manualState,
        route:routeState,
        player:playerState,

        view:{
            center:viewCenter,
            zoom:s,
            layer:layer,
            orientation:$('o').value,
            follow:$('fo').checked,
            autoLayer:$('au').checked,
            showTrace:$('tr').checked,
            showVolumes:$('vo').checked,
            categories:ON.map(Boolean)
        }
    };

    const clone=document.documentElement.cloneNode(true);

    clone.querySelectorAll('#etb-export-state').forEach(el=>{
        el.remove();
    });

    const bootstrap=document.createElement('script');

    bootstrap.id='etb-export-state';

    const json=JSON.stringify(exportState)
        .replace(/</g,'\\u003c');

    bootstrap.textContent=
        'window.__ETB_EMBEDDED_STATE__ = '+json+';';

    clone.querySelector('head').prepend(bootstrap);

    const html='<!doctype html>\n'+clone.outerHTML;

    const blob=new Blob(
        [html],
        {type:'text/html;charset=utf-8'}
    );

    const url=URL.createObjectURL(blob);

    const link=document.createElement('a');

    const filename=(document.title||'carte')
        .replace(/[^\w-]+/g,'_');

    link.href=url;
    link.download=filename+'_complete.html';

    document.body.appendChild(link);
    link.click();
    link.remove();

    setTimeout(()=>{
        URL.revokeObjectURL(url);
    },2000);
}


// ======================================================
// DESSIN : AJOUT DU CHEMIN AU DESSIN DE BASE
// ======================================================

const previousDraw=draw;

draw=function() {
    previousDraw();
    routeDrawOverlay();
};


// ======================================================
// SOURIS ET INTERACTIONS
// ======================================================

cv.onpointerdown=e=>{
    // Clic droit : déplacement prioritaire.
    if (e.button===2) {
        e.preventDefault();

        dr=[
            e.clientX,
            e.clientY,
            ox,
            oy
        ];

        cv.setPointerCapture(e.pointerId);

        $('fo').checked=false;

        return;
    }

    if (e.button!==0) return;

    const rect=cv.getBoundingClientRect();

    const mx=(e.clientX-rect.left)*(w/rect.width);
    const my=(e.clientY-rect.top)*(h/rect.height);

    // Le mode d'ajout des destinations est prioritaire.
    if (routeSelecting) {
        e.preventDefault();
        routeAddWaypoint(e);
        return;
    }

    // Tracé manuel.
    if (pathDrawing) {
        addPathPoint(e);
        return;
    }

    // Sélection d'un segment manuel.
    const hit=findPathSegment(mx,my);

    if (hit>=0) {
        selectPathSegment(hit);
        return;
    }

    selectedPathSegment=-1;

    updatePathUI();

    // Simulation de téléportation.
    if (LIVE && $('sim').checked) {
        const p=canvasToWorld(
            e.clientX,
            e.clientY
        );

        setPos({
            x:p.x,
            y:p.y,
            z:pl ? pl.z : 0,
            yaw:pl ? pl.yaw : 0
        });

        return;
    }

    // Déplacement normal de la carte.
    dr=[
        e.clientX,
        e.clientY,
        ox,
        oy
    ];

    cv.setPointerCapture(e.pointerId);

    $('fo').checked=false;
};


// Empêche le menu contextuel du navigateur.

cv.oncontextmenu=e=>{
    e.preventDefault();
};


cv.onpointerup=()=>{
    dr=null;
};

cv.onpointercancel=()=>{
    dr=null;
};


cv.onpointermove=e=>{
    lastTracePointer={
        x:e.clientX,
        y:e.clientY
    };

    const r=cv.getBoundingClientRect();

    const mx=(e.clientX-r.left)*(w/r.width);
    const my=(e.clientY-r.top)*(h/r.height);

    if (dr) {
        ox=dr[2]+e.clientX-dr[0];
        oy=dr[3]+e.clientY-dr[1];

        draw();
    }

    if (!LIVE && !raf) {
        raf=requestAnimationFrame(()=>{
            raf=0;

            const n=nearest(mx,my);

            $('tx').textContent=
                'X '+Math.round((mx-ox)/s)+
                '  Y '+Math.round(fy()*(my-oy)/s)+
                (n ? '   ›  '+n : '');
        });
    }
};


cv.onwheel=e=>{
    e.preventDefault();

    const r=cv.getBoundingClientRect();

    const f=$('fo').checked&&pl;

    const mx=f
        ? w/2
        : (e.clientX-r.left)*(w/r.width);

    const my=f
        ? h/2
        : (e.clientY-r.top)*(h/r.height);

    const z=e.deltaY<0 ? 1.2 : 1/1.2;

    ox=mx-(mx-ox)*z;
    oy=my-(my-oy)*z;

    s*=z;

    draw();
};


// ======================================================
// COMMANDES DES BARRES D'OUTILS
// ======================================================

// Affichage

['fo','au','tr','vo'].forEach(id=>{
    $(id).onchange=()=>{
        if (id==='fo' && $('fo').checked) {
            center();
        }

        if (id==='au' && $('au').checked && pl) {
            layer=lay(pl.z-ZOFF);
            $('ly').value=layer;
        }

        draw();
    };
});


$('ly').onchange=()=>{
    layer=+$('ly').value;
    $('au').checked=false;
    draw();
};


$('o').onchange=()=>{
    fit();
    center();
    draw();
};


$('r').onclick=()=>{
    fit();
    $('fo').checked=false;
};


$('cl').onclick=()=>{
    tr=[];
    draw();
};


// Tracé manuel

$('pathToggle').onclick=()=>{
    if (pathDrawing) {
        finishPathDrawing();
    } else {
        startPathDrawing();
    }
};


$('pathFinish').onclick=finishPathDrawing;

$('pathUndo').onclick=undoPathPoint;

$('pathDelete').onclick=deleteSelectedPathSegment;

$('pathClear').onclick=clearManualPath;

$('pathExport').onclick=exportManualPath;


// Chemin le plus court

$('routeSelect').onclick=routeToggleSelection;

$('routeCalculate').onclick=routeCalculate;

$('routeUndo').onclick=routeUndo;

$('routeClear').onclick=routeClear;


// Exports

$('png').onclick=()=>{
    cv.toBlob(b=>{
        if (!b) return;

        const a=document.createElement('a');

        const url=URL.createObjectURL(b);

        a.href=url;
        a.download='carte.png';

        a.click();

        setTimeout(()=>URL.revokeObjectURL(url),2000);
    });
};


$('routeExport').onclick=routeExport;

$('routeExportHTML').onclick=exportCompleteHTML;


// ======================================================
// RACCOURCIS CLAVIER
// ======================================================

addEventListener('keydown',event=>{
    if (
        event.target instanceof HTMLInputElement ||
        event.target instanceof HTMLTextAreaElement ||
        event.target instanceof HTMLSelectElement
    ) {
        return;
    }

    if (event.repeat) return;

    // T : commence ou ajoute un segment.
    if (event.key.toLowerCase()==='t') {
        event.preventDefault();

        if (!pathDrawing) {
            startPathDrawing();
        }

        if (lastTracePointer) {
            addPathPointAtClient(
                lastTracePointer.x,
                lastTracePointer.y
            );
        }

        return;
    }

    // Échap : termine le tracé manuel.
    if (event.key==='Escape') {
        if (pathDrawing) {
            event.preventDefault();
            finishPathDrawing();
            return;
        }

        if (routeSelecting) {
            event.preventDefault();
            routeSelecting=false;
            routeUpdateUI();
        }
    }

    // Retour arrière : annule un point manuel.
    if (
        event.key==='Backspace' &&
        pathDrawing
    ) {
        event.preventDefault();
        undoPathPoint();
    }

    // Entrée : termine la sélection des destinations.
    if (
        event.key==='Enter' &&
        routeSelecting
    ) {
        event.preventDefault();

        routeSelecting=false;
        routeUpdateUI();
    }
});


// ======================================================
// RESTAURATION D'UN HTML EXPORTÉ
// ======================================================

function restoreEmbeddedView() {
    const view=EMBEDDED.view;

    if (!view) return;

    if (view.orientation!=null) {
        $('o').value=String(view.orientation);
    }

    if (typeof view.follow==='boolean') {
        $('fo').checked=view.follow;
    }

    if (typeof view.autoLayer==='boolean') {
        $('au').checked=view.autoLayer;
    }

    if (typeof view.showTrace==='boolean') {
        $('tr').checked=view.showTrace;
    }

    if (typeof view.showVolumes==='boolean') {
        $('vo').checked=view.showVolumes;
    }

    if (
        Number.isInteger(view.layer) &&
        view.layer>=-1 &&
        view.layer<LBL.length
    ) {
        layer=view.layer;
        $('ly').value=String(layer);
    }

    if (
        Array.isArray(view.categories) &&
        view.categories.length===ON.length
    ) {
        ON=view.categories.map(Boolean);
        buildCats('');
    }

    if (
        Number.isFinite(view.zoom) &&
        view.zoom>0
    ) {
        s=view.zoom;
    }

    if (
        view.center &&
        Number.isFinite(view.center.x) &&
        Number.isFinite(view.center.y)
    ) {
        ox=w/2-view.center.x*s;
        oy=h/2-fy()*view.center.y*s;
    }

    draw();
}


// ======================================================
// INITIALISATION
// ======================================================

addEventListener('resize',()=>{
    fit();
});

fit();

restoreEmbeddedView();

updatePathUI();
routeUpdateUI();

if (LIVE) {
    poll();
}

</script>
</body>
</html>
'''


# ------------------------------------------------------------------
# RENDU HTML

def render(data, title, live):
    t = TEMPLATE

    for k in (
        'CATS',
        'GR',
        'L',
        'R',
        'D',
        'K',
        'V',
        'LBL',
        'LZ',
        'POL'
    ):
        t = t.replace(
            '__' + k + '__',
            json.dumps(
                data[k],
                separators=(',', ':'),
                ensure_ascii=False
            )
        )

    return (
        t.replace(
            '__LIVE__',
            'true' if live else 'false'
        )
        .replace('__TITLE__', title)
        .replace(
            '__HUD0__',
            'en attente de position…'
            if live
            else "Survole la carte : coordonnées et nom de l'objet"
        )
        .replace('__OPEN__', '5')
        .replace('__MAIN__', str(data.get('MAIN', 0)))
    )


# ------------------------------------------------------------------
# MAIN

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    ap.add_argument('json', nargs='?')

    ap.add_argument(
        '-o',
        '--out',
        help="préfixe des fichiers de sortie (défaut : nom du json)"
    )

    ap.add_argument('--title')

    ap.add_argument(
        '--max-layers',
        type=int,
        default=6
    )

    ap.add_argument(
        '--pivot',
        action='append',
        metavar='GROUPE=start|center|end'
    )

    ap.add_argument(
        '--tile-pivot',
        choices=['center', 'corner']
    )

    ap.add_argument(
        '--len',
        action='append',
        metavar='MESH=LONGUEUR'
    )

    ap.add_argument(
        '--meshes',
        action='append',
        metavar='ZIP|DOSSIER|JSON',
        help="exports FModel de StaticMesh (collision exacte des murs)"
    )

    ap.add_argument('--no-live', action='store_true')
    ap.add_argument('--no-open', action='store_true')

    # Correction de l'option no-live :
    ap.set_defaults(no_live=False)

    a = ap.parse_args()

    path = a.json
    interactive = False

    if not path:
        try:
            import tkinter as tk
            from tkinter import filedialog

            r = tk.Tk()
            r.withdraw()

            path = filedialog.askopenfilename(
                title="Choisis l'export FModel du niveau (.json)",
                filetypes=[('JSON', '*.json')]
            )

            interactive = True

        except Exception:
            ap.error("indique le fichier .json")

        if not path:
            return

    base = a.out or os.path.splitext(path)[0]

    title = (
        a.title or
        (os.path.splitext(os.path.basename(path))[0] + ' — carte 2D')
    )

    data = build(path, a)

    out1 = base + '_map.html'

    with open(out1, 'w', encoding='utf-8') as f:
        f.write(render(data, title, False))

    print('Carte  :', out1)

    if not a.no_live:
        out2 = base + '_live_map.html'

        with open(out2, 'w', encoding='utf-8') as f:
            f.write(
                render(
                    data,
                    title.replace('carte 2D', 'carte live'),
                    True
                )
            )

        print(
            'Live   :',
            out2,
            ' (à servir avec : python etb_live_map.py --map ' +
            os.path.basename(out2) + ')'
        )

    if not a.no_open and (interactive or len(sys.argv) == 1):
        webbrowser.open(
            'file:///' +
            os.path.abspath(out1).replace('\\', '/')
        )


if __name__ == '__main__':
    main()
