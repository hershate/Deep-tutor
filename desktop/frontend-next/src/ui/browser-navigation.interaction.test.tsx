// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import './testkit';
import { MockPort } from '../port/mock';
import type { WireEvent } from '../port/wire';
import { Pane } from './Pane';
import { openPage, unseenPageOpenings } from './browser_open';
const page = { id: 'b1', target: 't1', url: 'https://example.com', title: 'Existing page', active: true };
const filesShown = () => document.querySelector('.workbench-body')!.hasAttribute('data-files');
const paneProps = { title: 'w', active: true, visible: true, sideHost: null, side: false, onFocus() {}, onReport() {}, onSessionChanged() {}, pulse: 0, findPulse: 0, onSettings() {}, needsProject: false, onOpenProject() {}, onKeepHere() {}, theme: 'dark' as const, dockW: 560, dockMax: 880, onDockW() {} };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it.each(['before', 'after', 'agent-before-response', 'canceled-final-read', 'stale-blank', 'stale-intermediate', 'canceled-before-response'].flatMap(order => ['files', 'launcher'].map(choice => [order, choice])))('reconciles an early blank tab and final navigation (order=%s, choice=%s)', async (order, choice) => {
  const choiceShown = () => choice === 'files' ? filesShown() : !!document.querySelector('.workbench-launcher');
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  const port = new MockPort();
  let emit!: (event: WireEvent) => void, resolve!: (tab: typeof page) => void;
  const subscribe = port.subscribe.bind(port);
  vi.spyOn(port, 'subscribe').mockImplementation((onEvent, onGap, bootstrap) => { emit = onEvent; return subscribe(onEvent, onGap, bootstrap); });
  const readTabs = vi.spyOn(port, 'browserTabs').mockResolvedValue([]);
  vi.spyOn(port, 'browserOpen').mockReturnValue(new Promise(done => { resolve = done; }));
  const pending = openPage(port, page.url);
  function Harness() {
    const [manual, setManual] = useState(true);
    return <Pane {...paneProps} rt={{id: 'navigation-pane', base: '', root: '/w', name: 'w'}} port={port} manualBrowser={manual} onManualBrowser={setManual} />;
  }
  render(<Harness />);
  await waitFor(() => expect(readTabs).toHaveBeenCalledOnce());
  const tabsChanged = async (tabs: typeof page[]) => {
    readTabs.mockResolvedValue(tabs);
    await act(async () => { emit({kind:'browser_tabs_changed'} as WireEvent); });
  };
  await tabsChanged([{...page, url:'about:blank'}]);
  fireEvent.click(document.querySelector('[data-action="workbench.new"]')!);
  if (choice === 'files') fireEvent.click(document.querySelector('[data-action="workbench.launch-files"]')!);
  expect(choiceShown()).toBe(true);
  if (order.startsWith('stale-')) {
    let readStale!: (tabs: typeof page[]) => void;
    readTabs.mockReturnValueOnce(new Promise(done => {readStale=done;}));
    await act(async () => { emit({kind:'browser_tabs_changed'} as WireEvent); });
    readTabs.mockResolvedValue([page]);
    await act(async () => { resolve(page); await pending; });
    await act(async () => { readStale([{...page,url:order === 'stale-blank' ? 'about:blank' : page.url+'/intermediate'}]); });
    expect(choiceShown()).toBe(true);
  }
  if (order === 'after' || order === 'canceled-final-read') {
    await act(async () => { resolve(page); await pending; });
  }
  if (order === 'canceled-final-read' || order === 'canceled-before-response') {
    let readFinal!: (tabs: typeof page[]) => void;
    readTabs.mockReturnValueOnce(new Promise(done => {readFinal=done;}));
    await act(async () => { emit({kind:'browser_tabs_changed'} as WireEvent); });
    await tabsChanged([{...page,url:page.url+'/newer'}]);
    await act(async () => { readFinal([page]); });
  } else await tabsChanged([page]);
  if (order === 'agent-before-response') await tabsChanged([{...page,url:page.url+'/agent-navigation'}]);
  if (order !== 'after') await act(async () => { resolve(page); await pending; });
  expect(choiceShown()).toBe(order !== 'agent-before-response' && order !== 'canceled-final-read' && order !== 'canceled-before-response');
  expect(unseenPageOpenings(port)).toHaveLength(0);
  await tabsChanged([{...page, url:page.url+'/agent-navigation'}]);
  await waitFor(() => expect(choiceShown()).toBe(false));
});

it('does not let an older ambiguous follow outrun a newer genuine agent tab in the same response wait', async () => {
  vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(null);
  const port = new MockPort();
  let emit!: (event:WireEvent)=>void, complete!: (tab:typeof page)=>void;
  const subscribe=port.subscribe.bind(port);
  vi.spyOn(port,'subscribe').mockImplementation((onEvent,onGap,bootstrap)=>{emit=onEvent;return subscribe(onEvent,onGap,bootstrap);});
  const tabs=vi.spyOn(port,'browserTabs').mockResolvedValue([]);
  vi.spyOn(port,'browserOpen').mockReturnValue(new Promise(resolve=>{complete=resolve;}));
  const pending=openPage(port,page.url);
  function Harness() {
    const [manual,setManual]=useState(true);
    return <Pane {...paneProps} rt={{id:'newer-agent-review',base:'',root:'/w',name:'w'}} port={port} manualBrowser={manual} onManualBrowser={setManual}/>;
  }
  render(<Harness/>);
  await waitFor(()=>expect(tabs).toHaveBeenCalledOnce());
  tabs.mockResolvedValue([{...page,url:'about:blank'}]);
  await act(async()=>{emit({kind:'browser_tabs_changed'} as WireEvent);});
  fireEvent.click(document.querySelector('[data-action="workbench.new"]')!);
  fireEvent.click(document.querySelector('[data-action="workbench.launch-files"]')!);
  const later={...page,url:'https://genuine-later.example/'};
  tabs.mockResolvedValue([later]);
  await act(async()=>{emit({kind:'browser_tabs_changed'} as WireEvent);});
  const newest={...page,id:'b2',target:'t2',url:'https://newest-agent.example/',title:'Newest agent'};
  tabs.mockResolvedValue([{...later,active:false},newest]);
  await act(async()=>{emit({kind:'browser_tabs_changed'} as WireEvent);});
  let verify!: (value:typeof page[])=>void;
  tabs.mockReturnValueOnce(new Promise(resolve=>{verify=resolve;}));
  await act(async()=>{complete(page);await pending;});
  const selected=()=>document.querySelector('[data-target="browser:t2"]')?.closest('[role="tab"]')?.getAttribute('aria-selected');
  expect(selected()).toBe('true');
  await act(async()=>{verify([{...later,active:false},newest]);});
  expect(selected()).toBe('true');
});
