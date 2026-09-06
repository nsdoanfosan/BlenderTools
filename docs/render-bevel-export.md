# Render Bevel export

`Export > Export Render Bevels` is off by default, preserving existing exports.
Enable it for a static mesh to include Bevel modifiers that are enabled for render
but hidden in the viewport. Both FBX Apply Modifiers settings must also be enabled.

Completely disabled modifiers, collision meshes, linked objects and skeletal exports
retain their existing evaluation. The original viewport flags are restored after
success or failure. This option does not modify or save source geometry.
