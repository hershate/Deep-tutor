// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import './testkit';
import { MockPort } from '../port/mock';
import type { WireEvent } from '../port/wire';
import { Pane } from './Pane';
import { openPage, unseenPageOpenings } from './browser_open';
const props = { title: 'w', active: true, visible: true, sideHost: null, side: false, onFocus() {}, onReport() {}, onSessionChanged() {}, pulse: 0, findPulse: 0, onSettings() {}, needsProject: false, onOpenProject() {}, onKeepHere() {}, theme: 'dark' as const, dockW: 560, dockMax: 880, onDockW() {} };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it.each(['failed-read', 'stream-gap'].flatMap(missed => ['same-url', 'changed-url'].flatMap(activity => ['files', 'launcher'].map(choice => [missed, activity, choice]))))('follows later activity after %s (%s, %s)', async (missed, activity, choice) => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  const port = new MockPort();
  const first = { id: 'a', target: 'target-a', url: 'https://first.example/', title: 'First', active: true };
  const opened = { id: 'b', target: 'target-b', url: 'https://opened.example/', title: 'Opened', active: false };
  let emit!: (event: WireEvent) => void, gap!: () => void, complete!: (tab: typeof opened) => void;
  const subscribe = port.subscribe.bind(port);
  vi.spyOn(port, 'subscribe').mockImplementation((onEvent, onGap, bootstrap) => { emit = onEvent; gap = onGap!; return subscribe(onEvent, onGap, bootstrap); });
  const reads = vi.spyOn(port, 'browserTabs').mockResolvedValue([first]);
  vi.spyOn(port, 'browserOpen').mockReturnValue(new Promise(done => { complete = done; }));
  const pending = openPage(port, opened.url);
  function Harness() {
    const [manual, setManual] = useState(true);
    return <Pane {...props} rt={{id:'missed-read',base:'',root:'/w',name:'w'}} port={port} manualBrowser={manual} onManualBrowser={setManual} />;
  }
  render(<Harness />);
  await waitFor(() => expect(document.querySelector('[data-target="browser:target-a"]')).not.toBeNull());
  fireEvent.click(document.querySelector('[data-action="workbench.new"]')!);
  if (choice === 'files') fireEvent.click(document.querySelector('[data-action="workbench.launch-files"]')!);
  const choiceShown = () => choice === 'files' ? document.querySelector('.workbench-body')!.hasAttribute('data-files') : !!document.querySelector('.workbench-launcher');
  expect(choiceShown()).toBe(true);
  if (missed === 'failed-read') {
    reads.mockRejectedValueOnce(new Error('tab read failed'));
    await act(async () => { emit({kind:'browser_tabs_changed'} as WireEvent); });
  } else await act(async () => { gap(); });
  await act(async () => { complete(opened); await pending; });
  expect(choiceShown()).toBe(true);
  const later = {...opened, active:true, url:activity === 'same-url' ? opened.url : opened.url+'later'};
  reads.mockResolvedValue([{...first,active:false},later]);
  await act(async () => { emit({kind:'browser_tabs_changed'} as WireEvent); });
  expect(choiceShown()).toBe(false);
  expect(document.querySelector('[data-target="browser:target-b"]')?.closest('[role="tab"]')?.getAttribute('aria-selected')).toBe('true');
  expect(unseenPageOpenings(port)).toHaveLength(0);
});

it.each(['failed-read', 'stream-gap'].flatMap(missed => ['same-url', 'changed-url', 'deactivation'].flatMap(activity => ['before', 'after'].map(order => [missed, activity, order]))))('follows only later activity after %s (%s, response %s event)', async (missed, activity, order) => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  const port = new MockPort();
  const first = { id: 'a', target: 'target-a', url: 'https://first.example/', title: 'First', active: true };
  const opened = { id: 'b', target: 'target-b', url: 'https://opened.example/', title: 'Opened', active: activity === 'deactivation' };
  let emit!: (event: WireEvent) => void, gap!: () => void, complete!: (tab: typeof opened) => void;
  const subscribe = port.subscribe.bind(port);
  vi.spyOn(port, 'subscribe').mockImplementation((onEvent, onGap, bootstrap) => { emit = onEvent; gap = onGap!; return subscribe(onEvent, onGap, bootstrap); });
  const reads = vi.spyOn(port, 'browserTabs').mockResolvedValue(activity === 'deactivation' ? [] : [first]);
  vi.spyOn(port, 'browserOpen').mockReturnValue(new Promise(done => { complete = done; }));
  const pending = openPage(port, opened.url);
  function Harness({filesPulse}: {filesPulse:number}) {
    const [manual, setManual] = useState(true);
    return <Pane {...props} rt={{id:'missed-read',base:'',root:'/w',name:'w'}} port={port} filesPulse={filesPulse} manualBrowser={manual} onManualBrowser={setManual} />;
  }
  const {rerender} = render(<Harness filesPulse={0} />);
  await waitFor(() => expect(reads).toHaveBeenCalledOnce());
  await act(async () => {});
  rerender(<Harness filesPulse={1} />);
  const choiceShown = () => document.querySelector('.workbench-body')!.hasAttribute('data-files');
  expect(choiceShown()).toBe(true);
  if (missed === 'failed-read') {
    reads.mockRejectedValueOnce(new Error('tab read failed'));
    await act(async () => { emit({kind:'browser_tabs_changed'} as WireEvent); });
  } else await act(async () => { gap(); });
  if (order === 'before') await act(async () => { complete(opened); await pending; });
  expect(choiceShown()).toBe(true);
  const later = {...opened, active:activity !== 'deactivation', url:activity === 'changed-url' ? opened.url+'later' : opened.url};
  reads.mockResolvedValue([{...first,active:activity === 'deactivation'},later]);
  await act(async () => { emit({kind:'browser_tabs_changed'} as WireEvent); });
  if (order === 'after') await act(async () => { complete(opened); await pending; });
  expect(choiceShown()).toBe(false);
  expect(document.querySelector(`[data-target="browser:${activity === 'deactivation' ? first.target : opened.target}"]`)?.closest('[role="tab"]')?.getAttribute('aria-selected')).toBe('true');
  expect(unseenPageOpenings(port)).toHaveLength(0);
});
