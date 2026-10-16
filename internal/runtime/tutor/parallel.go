package tutor

import (
	"runtime"
	"sync"
)

// parallelFor runs fn over [0, n) on a bounded worker pool. Order lives
// with the caller: fn writes into caller-owned slots, so results merge
// deterministically no matter how the indices were divided.
func parallelFor(n int, fn func(i int)) {
	workers := min(n, min(runtime.GOMAXPROCS(0), 8))
	if workers <= 1 {
		for i := range n {
			fn(i)
		}
		return
	}
	idx := make(chan int)
	var wg sync.WaitGroup
	for range workers {
		wg.Go(func() {
			for i := range idx {
				fn(i)
			}
		})
	}
	go func() {
		for i := range n {
			idx <- i
		}
		close(idx)
	}()
	wg.Wait()
}
