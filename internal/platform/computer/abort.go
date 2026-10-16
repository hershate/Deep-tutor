package computer

import (
	"bufio"
	"encoding/json"
	"io"
	"net"
	"path/filepath"
	"time"
)

func abortEmbedded(socket string) error {
	if !filepath.IsAbs(socket) {
		return fail(CodeUnavailable, "CUA interruption socket must be absolute")
	}
	connection, err := net.DialTimeout("unix", socket, time.Second)
	if err != nil {
		return err
	}
	defer connection.Close()
	return exchangeAbort(connection)
}

func exchangeAbort(connection net.Conn) error {
	if err := connection.SetDeadline(time.Now().Add(12 * time.Second)); err != nil {
		return err
	}
	if _, err := connection.Write([]byte("{\"op\":\"abort\"}\n")); err != nil {
		return err
	}
	line, err := bufio.NewReader(io.LimitReader(connection, 16384)).ReadBytes('\n')
	if err != nil {
		return err
	}
	var reply struct {
		OK bool `json:"ok"`
	}
	if err := json.Unmarshal(line, &reply); err != nil {
		return err
	}
	if !reply.OK {
		return fail(CodeStopped, "Studio could not stop its owned CUA service")
	}
	return nil
}
