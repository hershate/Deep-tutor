package computer

import (
	_ "embed"
	"encoding/json"
)

//go:embed cua-driver.lock.json
var contractJSON []byte

var pinnedContract = decodeContract()

type DriverContract struct {
	Version    string `json:"version"`
	Bundle     string `json:"host_bundle_id"`
	Protocol   string `json:"protocol_version"`
	Contract   string `json:"contract_version"`
	Tools      string `json:"tools_list_schema_version"`
	Capability string `json:"capability_version"`
}

func Contract() DriverContract { return pinnedContract }

func decodeContract() DriverContract {
	var contract DriverContract
	if err := json.Unmarshal(contractJSON, &contract); err != nil {
		panic(err)
	}
	return contract
}
