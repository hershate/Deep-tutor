package computer

import (
	"bufio"
	"net"
	"testing"
)

func TestCUAAbortAcknowledgmentRequiresAnOwnedStop(t *testing.T) {
	for _, reply := range []string{`{"ok":true}`, `{"ok":false}`, `not json`, ``} {
		t.Run(reply, func(t *testing.T) {
			client, server := net.Pipe()
			defer client.Close()
			go func() {
				defer server.Close()
				_, _ = bufio.NewReader(server).ReadString('\n')
				if reply != "" {
					_, _ = server.Write([]byte(reply + "\n"))
				}
			}()
			err := exchangeAbort(client)
			if (err == nil) != (reply == `{"ok":true}`) {
				t.Fatalf("ack %q: %v", reply, err)
			}
		})
	}
	if err := abortEmbedded("relative"); CodeOf(err) != CodeUnavailable {
		t.Fatal(err)
	}
	if err := abortEmbedded("/missing/reasonix-abort.sock"); err == nil {
		t.Fatal("missing endpoint accepted")
	}
}
