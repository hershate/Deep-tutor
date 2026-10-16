// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { liveGraceMinutes, LIVE_GRACE_CHOICES, onLiveGraceChange, setLiveGraceMinutes } from "./prefs";

beforeEach(() => localStorage.clear());

describe("how long a finished session stays in the running list", () => {
  it("is off until this machine chooses otherwise", () => {
    expect(liveGraceMinutes()).toBe(0);
  });

  it("reads back a choice from the offered list", () => {
    for (const minutes of LIVE_GRACE_CHOICES) {
      setLiveGraceMinutes(minutes);
      expect(liveGraceMinutes()).toBe(minutes);
    }
  });

  it("treats anything unreadable, unoffered or negative as off", () => {
    for (const raw of ["", "abc", "-5", "7", "1e3", "30 "]) {
      localStorage.setItem("rx-live-grace", raw);
      expect(liveGraceMinutes(), raw).toBe(0);
    }
  });

  it("tells subscribers when the choice changes", () => {
    let heard = 0;
    const stop = onLiveGraceChange(() => heard++);
    setLiveGraceMinutes(30);
    stop();
    setLiveGraceMinutes(60);
    expect(heard).toBe(1);
  });
});
