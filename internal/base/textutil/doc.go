// Text that came from somewhere we do not control — an MCP server's own
// description of itself, a tool's docstring, a package manifest — is shown by
// one of the policies below, each declared for one kind of surface.
//
//	locator  ShownLocator, Literal      show every hidden character as \u{hex}
//	prose    BoundProse, ShownProse     delete them and report that text was removed
//	display  SanitizeDisplay            prose, collapsed to one line
//	launch   SanitizeLaunch             display that also deletes joiners, with a report
//	stored   SanitizeStored             delete controls and invisibles, keep lines
//	commit   StripHiddenControls        delete controls and bidi only, keep joiners
//
// Deleting policies share one core, so a character is judged the same way
// wherever it is met and a report never disagrees with what was deleted.
package textutil
