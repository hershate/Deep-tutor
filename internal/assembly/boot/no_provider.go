package boot

import (
	// The stand-in provider kind registers itself; a build that may need it must
	// not depend on which binary imported what.
	_ "reasonix/internal/model/noprovider"
)
