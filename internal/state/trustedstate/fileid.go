package trustedstate

// fileID is the identity a cache hit must still see: inode, device and change
// time, which a writer cannot restore the way it can a modification time.
// Where the platform offers none of them (Windows, and systems without a
// matching Stat_t) it is the zero value and the cache falls back to size and
// modification time alone.
type fileID struct {
	dev, ino uint64
	ctimeNs  int64
}

// known reports whether the platform supplied any identity at all.
func (f fileID) known() bool { return f.dev != 0 || f.ino != 0 || f.ctimeNs != 0 }
