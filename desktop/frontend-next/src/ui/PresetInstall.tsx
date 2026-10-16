import { useRef, useState } from "react";
import { t } from "../i18n";
import { reason } from "../i18n/kernel";
import { HttpError } from "../port/http_error";
import type { ProviderPreset } from "../port/port";
import type { Port } from "./Providers";
import { KIND_LABEL, hostOf, sourceNameUsable } from "./vendors";

const NAME_CODES = new Set(["provider.name_required", "provider.name_invalid", "provider.name_taken"]);

export function PresetInstall({ port, preset, onDone, onCancel, onBack }: {
  port: Port; preset: ProviderPreset; onDone: () => void; onCancel: () => void; onBack: () => void;
}) {
  const [name, setName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [nameErr, setNameErr] = useState("");
  const keyBox = useRef<HTMLInputElement>(null);
  const nameBad = name.trim() !== "" && !sourceNameUsable(name);

  const install = async () => {
    setBusy(true);
    setErr("");
    setNameErr("");
    try {
      await port.installProviderPreset({ presetId: preset.id, name: name.trim(), apiKey: apiKey.trim() });
      onDone();
    } catch (e) {
      if (e instanceof HttpError && e.reason?.code && NAME_CODES.has(e.reason.code)) setNameErr(reason(e));
      else setErr(reason(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <fieldset className="addp addp-preset-form" disabled={busy}>
      <div className="addp-head">
        <div>
          <span className="step">{t(KIND_LABEL[preset.kind] ?? preset.kind)} · {hostOf(preset.baseUrl)}</span>
          <strong>{preset.label}</strong>
          <p>{t("模型目录、上下文窗口和思考设置由内核按这个服务的预设一并创建。")}</p>
        </div>
        <button className="addp-close" data-action="provider.preset-cancel" onClick={onCancel} aria-label={t("取消")}>×</button>
      </div>
      <div className="fields">
        <label className="grow">
          <span>{t("来源名称")}</span>
          <input aria-label={t("来源名称")} data-action="provider.draft" data-value="preset-name" value={name} placeholder={t("预设自带的名称")} onChange={(e) => { setName(e.target.value); setNameErr(""); }} spellCheck={false} aria-invalid={nameBad || nameErr !== "" || undefined} />
          <i className="tip" data-bad={nameBad || nameErr !== "" || undefined}>{nameErr !== "" ? nameErr : t("留空就用预设自带的名称；重名时内核会自动编号。")}</i>
        </label>
        <label className="grow full">
          <span>API Key</span>
          <input ref={keyBox} autoFocus type="password" data-action="provider.draft" data-value="preset-credential" aria-label="API Key" value={apiKey} onChange={(e) => setApiKey(e.target.value)} spellCheck={false} />
        </label>
        <p className="addp-privacy">{t("API Key 仅保存在运行内核的这台机器上。")}</p>
      </div>
      {err !== "" && (
        <div className="find" data-lvl="warn" role="alert">
          <span className="t">{t("无法保存")}</span>
          <span className="why">{err}</span>
        </div>
      )}
      <div className="acts addp-footer">
        <button className="act" data-action="provider.add" data-primary onClick={install} disabled={busy || nameBad}>{t(busy ? "保存中…" : "添加来源")}</button>
        <button className="act" data-action="provider.preset-back" onClick={onBack}>{t("换一个")}</button>
        <button className="act" data-action="provider.preset-cancel" onClick={onCancel}>{t("取消")}</button>
      </div>
    </fieldset>
  );
}
