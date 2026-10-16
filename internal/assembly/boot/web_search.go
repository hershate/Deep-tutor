package boot

import (
	"reasonix/internal/base/netclient"
	"reasonix/internal/contract/config"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
	"reasonix/internal/contract/tool"
	"reasonix/internal/tools/websearch"
)

var searchUnavailableNotice = map[config.WebSearchReason]string{
	config.WebSearchBadRef:        event.NoticeCodeWebSearchModelBadRef,
	config.WebSearchNotAdded:      event.NoticeCodeWebSearchModelNotAdded,
	config.WebSearchModelRemoved:  event.NoticeCodeWebSearchModelRemoved,
	config.WebSearchUnsupported:   event.NoticeCodeWebSearchModelUnsupported,
	config.WebSearchNoCredentials: event.NoticeCodeWebSearchModelNoCredentials,
}

func addWebSearch(reg *tool.Registry, cfg *config.Config, proxy netclient.ProxySpec, sink event.Sink) {
	selection := cfg.ResolveWebSearch()
	if selection.Err != nil {
		reason, _ := config.WebSearchReasonOf(selection.Err)
		report(sink, event.Event{Level: event.LevelInfo, Code: searchUnavailableNotice[reason],
			Text: "The selected web search model is unavailable. Choose another search model or automatic selection in Model preferences.", Detail: cfg.Agent.WebSearchModel})
	}
	entry := selection.Entry
	if entry == nil {
		return
	}
	reg.Add(&websearch.Tool{
		Factory: func() (provider.Provider, error) {
			return newWebSearchProvider(entry, proxy)
		},
		ReportUsage: func(usage *provider.Usage) {
			if sink != nil {
				sink.Emit(event.Event{Kind: event.Usage, ModelRef: modelRefFromEntry(entry), Usage: usage, Pricing: entry.Price, UsageSource: "web-search"})
			}
		},
	})
}

// newWebSearchProvider builds the routed entry's provider with the
// provider-side search tool forced on and redirects rejected.
func newWebSearchProvider(entry *config.ProviderEntry, proxy netclient.ProxySpec) (provider.Provider, error) {
	cfg := providerConfig(entry, proxy)
	cfg.Extra["web_search"] = true
	cfg.Extra["reject_redirects"] = true
	return provider.New(entry.Kind, cfg)
}

// NewWebSearchModel resolves the configured web-search model and answers its
// provider plus the ref its usage prices under. The registry's web_search
// tool and the tutor surface both resolve through here, so one selection
// serves every search. Unset or "auto" answers (nil, "", nil); a selection
// that cannot serve answers one of Reasonix's web-search reasons.
func NewWebSearchModel(cfg *config.Config, proxy netclient.ProxySpec) (provider.Provider, string, error) {
	selection := cfg.ResolveWebSearch()
	if selection.Err != nil {
		return nil, "", selection.Err
	}
	if selection.Entry == nil {
		return nil, "", nil
	}
	p, err := newWebSearchProvider(selection.Entry, proxy)
	if err != nil {
		return nil, "", err
	}
	return p, selection.Entry.Name + "/" + selection.Entry.Model, nil
}
