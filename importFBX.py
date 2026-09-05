# importFBX.py — Autodesk FBX mesh importer for FreeCAD.
#
# Entry points required by FreeCAD's file pipeline:
#   open(filename)          -> File -> Open (creates a fresh document)
#   insert(filename, docname) -> File -> Import (injects into existing doc)
#
# Dependency: pyassimp + system Assimp library.
#   Ubuntu/Debian: sudo apt install libassimp-dev
#   Arch:          sudo pacman -S assimp
#   macOS:         brew install assimp
#   Then, in FreeCAD's Python runtime: pip install pyassimp

import os
import re
import FreeCAD as App
import Mesh

try:
    import pyassimp
    from pyassimp.postprocess import (
        aiProcess_Triangulate,
        aiProcess_JoinIdenticalVertices,
        aiProcess_GenNormals,
    )
    HAS_ASSIMP = True
except ImportError:
    HAS_ASSIMP = False

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


def _iter_instances(scene, meshes):
    """Yield (name, mesh, matrix) for every mesh instance in the scene graph.

    Mesh vertices are stored in node-local space; each node's transformation
    places the part in the assembly. Ignoring the node tree imports every part
    at the origin (exploded assembly), so the world transform is accumulated
    per instance. Falls back to the flat mesh list when the scene has no node
    hierarchy.
    """
    root = getattr(scene, "rootnode", None)
    if root is None or not hasattr(root, "children"):
        for idx, mesh in enumerate(meshes):
            raw = getattr(mesh, "name", "") or "FBX_Part_{:03d}".format(idx)
            yield raw, mesh, None
        return

    identity = np.identity(4, dtype=np.float64) if HAS_NUMPY else None
    stack = [(root, identity)]
    while stack:
        node, parent_tm = stack.pop()
        world = parent_tm
        if HAS_NUMPY:
            local_raw = getattr(node, "transformation", None)
            if local_raw is not None:
                try:
                    world = parent_tm @ np.array(local_raw,
                                                 dtype=np.float64).reshape(4, 4)
                except (TypeError, ValueError):
                    world = parent_tm
        for mesh in getattr(node, "meshes", None) or []:
            raw = (getattr(node, "name", "")
                   or getattr(mesh, "name", "")
                   or "FBX_Part")
            yield raw, mesh, world
        for child in getattr(node, "children", None) or []:
            stack.append((child, world))


def _place_vertices(vertices, matrix):
    """Apply a 4x4 world transform to an (N, 3) vertex array. Returns the
    input unchanged when there is nothing to apply."""
    if matrix is None or not HAS_NUMPY or vertices is None:
        return vertices
    try:
        if np.allclose(matrix, np.identity(4, dtype=np.float64)):
            return vertices
        verts = np.asarray(vertices, dtype=np.float64)
        if verts.ndim != 2 or verts.shape[1] < 3:
            return vertices
        hom = np.hstack([verts[:, :3],
                         np.ones((len(verts), 1), dtype=np.float64)])
        return (hom @ matrix.T)[:, :3]
    except (TypeError, ValueError):
        return vertices


def open(filename):
    """Callback for File -> Open. Creates a fresh document."""
    base_name = os.path.splitext(os.path.basename(filename))[0]
    # Sanitize: FreeCAD document names must not contain some characters.
    base_name = re.sub(r"[^A-Za-z0-9_]", "_", base_name) or "FBX_Document"
    doc = App.newDocument(base_name)
    insert(filename, doc.Name)
    return doc


def insert(filename, docname):
    """Callback for File -> Import. Injects geometry into existing document."""
    if not HAS_ASSIMP:
        App.Console.PrintError(
            "FBXImporter Error: 'pyassimp' is not installed in FreeCAD's Python environment.\n"
            "Run 'pip install pyassimp' in your FreeCAD Python runtime to enable FBX loading.\n"
        )
        return

    doc = App.getDocument(docname)
    if not doc:
        doc = App.newDocument(docname)

    App.Console.PrintMessage("Parsing FBX: {}...\n".format(filename))

    # Post-processing flags:
    # 1. Triangulate quads/n-gons so FreeCAD's Mesh engine can ingest them
    # 2. Join duplicate vertices to reduce memory overhead
    # 3. Generate normals if the file lacks them (harmless for Mesh::Feature)
    flags = aiProcess_Triangulate | aiProcess_JoinIdenticalVertices | aiProcess_GenNormals

    scene_mgr = None
    scene = None
    release_scene = None
    transaction_open = False
    try:
        # pyassimp has two APIs across versions:
        #   4.x: scene = pyassimp.load(...) + pyassimp.release(scene)
        #   5.x: load() returns a context manager (with ... as scene)
        # Support both so neither fails silently with an empty scene.
        scene_mgr = pyassimp.load(filename, processing=flags)
        if hasattr(scene_mgr, "__enter__"):
            scene = scene_mgr.__enter__()
            release_scene = lambda: scene_mgr.__exit__(None, None, None)
        else:
            scene = scene_mgr
            release_scene = lambda: pyassimp.release(scene)

        meshes = getattr(scene, "meshes", None) or []
        instances = list(_iter_instances(scene, meshes))
        if not instances:
            App.Console.PrintWarning("FBXImporter: No mesh data found in scene.\n")
            return

        App.Console.PrintMessage(
            "Found {} mesh instance(s). Building FreeCAD objects...\n".format(len(instances))
        )

        # FreeCAD document transactions ensure clean undo/redo history
        doc.openTransaction("Import FBX")
        transaction_open = True

        used_names = set()
        for raw_name, mesh, matrix in instances:
            # FreeCAD object labels can be free-form, but internal names
            # must be valid and unique; addObject() sanitizes, still strip empties.
            base = re.sub(r"[^A-Za-z0-9_]", "_", raw_name).strip("_") or "FBX_Part"
            mesh_name = base
            suffix = 0
            while mesh_name in used_names:
                suffix += 1
                mesh_name = "{}_{:03d}".format(base, suffix)
            used_names.add(mesh_name)

            vertices = _place_vertices(mesh.vertices, matrix)
            faces = mesh.faces

            facets = []
            for face in faces:
                # Guaranteed triangles due to aiProcess_Triangulate,
                # but guard against degenerate / non-tri faces.
                if len(face) != 3:
                    continue
                try:
                    v1 = App.Vector(float(vertices[face[0]][0]),
                                    float(vertices[face[0]][1]),
                                    float(vertices[face[0]][2]))
                    v2 = App.Vector(float(vertices[face[1]][0]),
                                    float(vertices[face[1]][1]),
                                    float(vertices[face[1]][2]))
                    v3 = App.Vector(float(vertices[face[2]][0]),
                                    float(vertices[face[2]][1]),
                                    float(vertices[face[2]][2]))
                except (IndexError, TypeError, ValueError):
                    continue
                facets.append([v1, v2, v3])

            if not facets:
                App.Console.PrintWarning(
                    "FBXImporter: mesh '{}' has no triangular faces, skipped.\n".format(raw_name)
                )
                continue

            # Batch-construct: far faster than addFacet() in a Python loop.
            fc_mesh = Mesh.Mesh(facets)

            # Create native FreeCAD Mesh Feature
            obj = doc.addObject("Mesh::Feature", mesh_name)
            obj.Label = raw_name
            obj.Mesh = fc_mesh

        doc.commitTransaction()
        transaction_open = False
        doc.recompute()
        App.Console.PrintMessage("FBX import completed successfully.\n")

    except Exception as exc:
        if transaction_open and doc:
            doc.abortTransaction()
        App.Console.PrintError("FBXImporter failed to parse file: {}\n".format(str(exc)))
        raise
    finally:
        if release_scene is not None:
            try:
                release_scene()
            except Exception:
                pass
