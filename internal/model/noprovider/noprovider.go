// Package noprovider is the provider a runtime holds while the user config lists
// none. It answers every request with provider.ErrNoProvider, so the first turn
// says what is missing instead of the kernel refusing to start.
package noprovider

import (
	"context"

	"reasonix/internal/contract/config"
	"reasonix/internal/contract/provider"
)

func init() {
	provider.Register(config.UnconfiguredKind, func(provider.Config) (provider.Provider, error) { return stub{}, nil })
}

type stub struct{}

func (stub) Name() string { return config.UnconfiguredName }

func (stub) Stream(context.Context, provider.Request) (<-chan provider.Chunk, error) {
	return nil, provider.ErrNoProvider
}
