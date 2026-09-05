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
        if not meshes:
            App.Console.PrintWarning("FBXImporter: No mesh data found in scene.\n")
            return

        App.Console.PrintMessage(
            "Found {} mesh(es). Building FreeCAD objects...\n".format(len(meshes))
        )

        # FreeCAD document transactions ensure clean undo/redo history
        doc.openTransaction("Import FBX")
        transaction_open = True

        for idx, mesh in enumerate(meshes):
            raw_name = getattr(mesh, "name", "") or "FBX_Part_{:03d}".format(idx)
            # FreeCAD object labels can be free-form, but internal names
            # must be valid; addObject() sanitizes, still strip empties.
            mesh_name = re.sub(r"[^A-Za-z0-9_]", "_", raw_name).strip("_") or "FBX_Part_{:03d}".format(idx)

            vertices = mesh.vertices
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
