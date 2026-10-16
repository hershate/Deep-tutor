package builtin

import "context"

type editReadPolicy struct {
	readRoots, forbidRoots []string
}

func (p editReadPolicy) source(ctx context.Context, overlay FileOverlay, path string) (editSource, error) {
	if err := checkReadTargetAccess(p.readRoots, p.forbidRoots, path); err != nil {
		return editSource{}, err
	}
	return readEditSource(ctx, overlay, path)
}
