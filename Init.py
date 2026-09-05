# FBXImporter Init.py — CLI (non-GUI) registration.
# Loaded by FreeCAD on startup, including console mode.
# Registers *.fbx with the import pipeline so File -> Open / Import works.

import FreeCAD

FreeCAD.addImportType("Autodesk FilmBox (*.fbx)", "importFBX")
