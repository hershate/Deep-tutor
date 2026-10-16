"use strict";
const net = require("node:net");
const args = process.argv.slice(2);
const socket = args[args.indexOf("--socket") + 1];
// The socket must exist at 0600 from the first instant: a reader that looks between listen and a later chmod sees 0755.
process.umask(0o177);
const server = net.createServer(peer => {
  peer.on("data", () => peer.end(JSON.stringify({ ok: true, result: { pid: process.pid, embedded: true, host_bundle_id: "io.reasonix.studio", driver_version: "0.34.1", contract_version: "0.8.0", tools_list_schema_version: "1", capability_version: "1", mcp_protocol_version: "2025-06-18" } }) + "\n"));
});
server.listen(socket);
process.stdin.resume();
process.stdin.on("end", () => server.close(() => process.exit(0)));
