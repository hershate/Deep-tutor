import type { ReactNode } from "react";
import type { AgentPort } from "../port/port";
import { Tutor, type TutorView } from "./tutor/Tutor";

interface Props {
  port: AgentPort;
  view: TutorView;
  onGo: (view: TutorView) => void;
  onChat: () => void;
  onSettings: () => void;
  onError: (message: string) => void;
  /** The window's error bar. The chat surface parks it on the pane in front;
   *  here the tutor is the front, so a refusal lands on its corner. */
  notice: ReactNode;
}

// The window's home surface: the study tutor fills the main area, and its
// header carries the exits — the app settings, and the agent chat where
// Reasonix's full surface lives.
export function TutorSurface({ port, view, onGo, onChat, onSettings, onError, notice }: Props) {
  return (
    <div className="tut-surface">
      <Tutor port={port} view={view} onGo={onGo} onChat={onChat} onSettings={onSettings} onError={onError} />
      {notice}
    </div>
  );
}
