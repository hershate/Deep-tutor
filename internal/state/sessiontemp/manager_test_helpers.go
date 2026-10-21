package sessiontemp

import "deep-tutor/internal/base/filelock"

func tryLockForTest(path string) (func(), error) {
	return filelock.Acquire(nilContext(), path)
}
