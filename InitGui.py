# FBXImporter InitGui.py — GUI registration.
# Loaded by FreeCADGui on startup. Registers the file type for the GUI
# and provides a minimal workbench stub to satisfy package.xml
# (<classname>FBXImporterWorkbench</classname>).

import FreeCAD
import FreeCADGui

FreeCAD.addImportType("Autodesk FilmBox (*.fbx)", "importFBX")


class FBXImporterWorkbench(FreeCADGui.Workbench):
    MenuText = "FBX Importer"
    ToolTip = "Autodesk FBX mesh importer (File -> Open / Import, no toolbar)"
    Icon = ""

    def Initialize(self):
        # Importer-only addon: no commands, toolbars, or menus.
        # File-type registration above is all the GUI needs.
        pass

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(FBXImporterWorkbench())
