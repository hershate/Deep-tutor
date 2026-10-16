---
owner: @esengine
backup: @SivanCola
status: active
reviewed: 2026-10-13
---

# Computer use through CUA Driver

The Studio host owns one CUA Driver and injects it explicitly into runtime assembly.

Independent headless builds have no ambient computer capability. Tabs share the host driver while each session owns its observations and references.

Model changes, reloads and workspace switches retain the host driver; closing a controller does not close another tab's driver. Host exit owns final cleanup.

The canonical embedded manifest is internal/platform/computer/cua-driver.lock.json. Go, Electron readiness and artifact installation read that contract; the packaged shell receives the same file as a resource.

Reasonix Studio packages the maintained Rust CUA Driver 0.34.1 on macOS, Windows and Linux.

The release is marked prerelease upstream; its exact artifact names and SHA-256 digests are fixed in the Electron packaging lock.

The older Swift-only stable release is not protocol compatible with this integration.

No self-update, perception-model installation or remote desktop service is enabled.

The bundled driver is MIT licensed; its notice ships with the desktop assets.

Reasonix owns application authorization, forbidden applications, separately approved foreground pointer input, session ownership and trajectory projection.

CUA owns OS accessibility, capture, input delivery and cursor implementation.

Windows/Linux executable identity is read from the OS so a display alias cannot hide a forbidden executable.

Windows desktop identities use the OS executable basename even when CUA returns a full launch path; packaged-app identities remain unchanged. Existing full-path permission rules may require a new approval for the canonical identity.

Application discovery joins one fresh window enumeration to the running-app catalogue by PID. A targeted observation or action queries only the selected process's windows after checking its unique identity; it never caches application authority.

Installed apps without targetable processes remain in application discovery. They never receive reads or input until a fresh process/window binding exists.

computer_act launch is one explicit step under the exact installed identity's approval. It uses only the native catalogue and CUA launch_app; no parameters, URL opens, mixed input batches or implicit launch-on-read are accepted.

Only a uniquely selected launcher receives filesystem verification. Windows requires an existing local executable, resolves links and checks the final PE image.

Linux requires one absolute executable without arguments or command syntax and verifies the resolved ELF image. Scripts and forbidden actual executables are refused before launch.

macOS uses the registered native bundle identity. Windows package launch is unsupported until its executable can be attested before activation; running packaged apps remain readable under their existing scope.

Launch invalidates old refs/captures. Its delivered request remains effect-unverified; the returned process must attest the exact executable or native bundle, and its PID must match before windows are read.

Launch returns the verified running identity without automatically taking a snapshot under a different identity's approval.

The model sees only computer_read and computer_act.

A multiwindow application requires an explicit window id.

Every host-issued element reference is fresh and maps to a CUA token inside one session/window/driver generation.

Screenshot coordinates are converted from the fitted model image to CUA capture pixels; point clicks require and consume the exact capture id.

A refresh, malformed observation, cancellation or reconnect invalidates old observations.

Text snapshots request only the accessibility tree; screenshots request only the captured image. Either refresh invalidates the previous refs and pixel frame. Image fitting and pixel binding still use the exact returned native capture dimensions.

All ordinary actions request background delivery.

Each driver generation loads its native tools/list argument contract. A missing delivery_mode is compatible only with the pinned always-background set_value operation; other incompatible operations are refused before dispatch.

A refusal never grants a foreground retry.

pointer_click retains the separate foreground approval subject.

The adapter does not emulate focus, held keys, pointer movement/drag/position through native shims.

These older calls return computer.unsupported and are absent from the advertised action catalogue.

Paste delegates text delivery to CUA; physical-pointer restoration is not promised by this adapter.

Delivery and target effect are separate.

CUA confirmed+value_readback maps to Reasonix confirmed; window-change-only, partial, suspected-noop and unverifiable results retain conservative effect-unverified status and the raw delivery facts.

An unverified step is recorded and the batch continues with the effect shown to the model; a partial or suspected-noop step is recorded and then stops the batch for fresh inspection.

The pinned macOS set_value result uses path=ax. The adapter retains that field and maps its route to accessibility without adopting the legacy verified flag or inventing value_readback evidence.

CUA does not provide the former helper's structured modal identity; Reasonix never invents that identity from a title or summary.

macOS uses an Electron-owned private embedded service.

Readiness checks socket permissions plus exact PID, bundle and protocol/version metadata.

A private abort-only control endpoint kills the owned service before restarting it and acknowledging an uncertain MCP cancellation.

This is required because upstream transport disconnect alone waits for blocking native input to unwind.

Failed stop acknowledgment disables reconnection.

Quit awaits service cleanup.

The released universal driver requires macOS 13, matching the Studio floor.

Driver telemetry is disabled for the owned processes without editing the user's CUA settings.

Secrets and driver configuration remain outside the model schema.

The native Swift/C++ helper and its build requirements are removed.

## Validation

Windows live checks use a source-built native application: Unicode value changes, window capture, element/pixel clicks, foreground pointer clicks and unchanged clipboard text.

Protocol fixtures cover stale/cross-session references, fitted capture pixels, ambiguous windows, malformed/oversized responses, uncertain cancellation and conservative effect evidence.

Embedded lifecycle fixtures test owned-process interruption and restart.

The Studio native acceptance runner installs the pinned artifact and exercises owned native targets on Windows, GTK under Linux X11/AT-SPI, and the actual embedded daemon with a source-built macOS app.

Linux X11 success does not establish Wayland behavior. Node-launched macOS CI exercises the embedding and native protocol, but signed Studio.app permission attribution and fresh-install grants require manual macOS release acceptance.
