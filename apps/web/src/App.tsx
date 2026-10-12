import { useCallback, useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import { PipecatClient } from '@pipecat-ai/client-js';
import { SmallWebRTCTransport } from '@pipecat-ai/small-webrtc-transport';
import { PipecatClientProvider, usePipecatClientMediaTrack } from '@pipecat-ai/client-react';
import { Stage } from './Stage';
import type { Mood, Motion, Phase } from './Stage';

const phaseName = { idle: '等你开口', listening: '正在听你说', thinking: '想一想…', speaking: '米拉正在说话' };
const presets = ['你在等谁？', '可以看看那张照片吗？', '我陪你再等一会儿。', '你可以给朋友发条消息。'];
type Scene = { photo_shown: boolean; lighting: string; ending_choice: string | null; revision: number };
const initialScene: Scene = { photo_shown: false, lighting: 'cool', ending_choice: null, revision: 0 };
type Message = { type: string; session_id: string; generation: number; text?: string; state?: Phase; scene?: Scene; message?: string; command_id?: string; payload?: { event: string; emotion: Mood; action: Motion; broken?: boolean } };

function BotAudio({ audio, onBlocked }: { audio: RefObject<HTMLAudioElement | null>; onBlocked: () => void }) {
  const track = usePipecatClientMediaTrack('audio', 'bot');
  useEffect(() => {
    const element = audio.current;
    if (!track || !element) return;
    element.srcObject = new MediaStream([track]);
    void element.play().catch(onBlocked);
    return () => { element.srcObject = null; };
  }, [track, audio, onBlocked]);
  return <audio ref={audio} autoPlay muted playsInline data-testid="bot-audio" />;
}

export default function App() {
  const [client, setClient] = useState<PipecatClient | null>(null);
  const current = useRef<PipecatClient | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const epoch = useRef(0);
  const generation = useRef(0);
  const sessionId = useRef('');
  const holding = useRef(false);
  const deadline = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const cleanupDeadline = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const cleared = useRef(0);
  const newAudio = useRef(0);
  const confirmed = useRef(initialScene);
  const accepted = useRef(new Map<number, (ok: boolean) => void>());
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [mode, setMode] = useState<'mock' | 'live'>('mock');
  const [liveAvailable, setLiveAvailable] = useState(false);
  const [phase, setPhase] = useState<Phase>('idle');
  const [mood, setMood] = useState<Mood>('guarded');
  const [motion, setMotion] = useState<Motion>('none');
  const [scene, setScene] = useState(initialScene);
  const [preview, setPreview] = useState(false);
  const [subtitle, setSubtitle] = useState('');
  const [heard, setHeard] = useState('');
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [blocked, setBlocked] = useState(false);
  const [preset, setPreset] = useState(presets[0]);
  const [fault, setFault] = useState('none');
  const [debug, setDebug] = useState(false);
  const [visibleGeneration, setVisibleGeneration] = useState(0);
  const onAudioBlocked = useCallback(() => setBlocked(true), []);

  useEffect(() => {
    void fetch('/api/health').then(r => r.json()).then(v => setLiveAvailable(v.live_available)).catch(error => { console.error('[健康检查]', error); setError('服务尚未启动，请稍后再试。'); });
    return () => { epoch.current++; clearTimeout(deadline.current); clearTimeout(cleanupDeadline.current); void current.current?.disconnect(); };
  }, []);

  useEffect(() => {
    if (error) console.error(`[交互] 会话=${sessionId.current || '-'} 轮次=${generation.current}`, error);
  }, [error]);

  function allowAudio() {
    if (audio.current && cleared.current === generation.current && newAudio.current === generation.current) audio.current.muted = false;
  }

  function snapshot(value: Scene) {
    if (value.revision >= confirmed.current.revision) {
      confirmed.current = value;
      setScene(value);
      setPreview(false);
    }
  }

  function message(value: Message, connectionEpoch: number) {
    if (connectionEpoch !== epoch.current || value.generation !== generation.current) return;
    if (sessionId.current && value.session_id !== sessionId.current) return;
    sessionId.current = value.session_id;
    if (value.type === 'input.accepted' || value.type === 'scene.snapshot') {
      if (value.type === 'input.accepted') {
        accepted.current.get(value.generation)?.(true);
        accepted.current.delete(value.generation);
      }
      if (value.scene) snapshot(value.scene);
    } else if (value.type === 'interaction.interrupted') {
      cleared.current = value.generation;
      clearTimeout(cleanupDeadline.current);
      allowAudio();
    } else if (value.type === 'response.state' && value.state) {
      if (!holding.current) setPhase(value.state);
      if (value.state === 'speaking') { newAudio.current = value.generation; allowAudio(); }
    } else if (value.type === 'subtitle.update') setSubtitle(value.text ?? '');
    else if (value.type === 'input.transcribed') setHeard(value.text ?? '');
    else if (value.type === 'error') { setError(value.message ?? '请重新试一次。'); setPhase('idle'); }
    else if (value.type === 'scene.command' && value.payload) {
      const command = value.payload;
      if (command.event === 'character') { setMood(command.emotion); setMotion(command.action); }
      else if (command.event === 'photo.reveal') {
        const origin = value.generation;
        const image = new Image();
        image.onload = () => {
          if (epoch.current !== connectionEpoch || generation.current !== origin) return;
          setMood(command.emotion); setMotion(command.action); setPreview(true);
          // Commit only after the loaded image has had a paint opportunity.
          requestAnimationFrame(() => requestAnimationFrame(() => {
            if (generation.current === origin && epoch.current === connectionEpoch)
              current.current?.sendClientMessage('scene.applied', { generation: origin, command_id: value.command_id, ok: true });
          }));
        };
        image.onerror = () => {
          if (generation.current !== origin || epoch.current !== connectionEpoch) return;
          setError('照片暂时无法加载，可以再问一次。');
          current.current?.sendClientMessage('scene.applied', { generation: origin, command_id: value.command_id, ok: false });
        };
        image.src = command.broken ? '/assets/missing-photo.svg' : '/assets/photo.svg';
      }
    }
  }

  async function connect() {
    setConnecting(true); setError('');
    const connectionEpoch = ++epoch.current;
    generation.current = 0; sessionId.current = ''; confirmed.current = initialScene;
    cleared.current = 0; newAudio.current = 0;
    setScene(initialScene); setPreview(false); setSubtitle(''); setHeard(''); setMood('guarded'); setMotion('none'); setPhase('idle');
    const value = new PipecatClient({ transport: new SmallWebRTCTransport(), enableMic: false, enableCam: false,
      callbacks: {
        onServerMessage: data => message(data as Message, connectionEpoch),
        onDisconnected: () => { if (epoch.current === connectionEpoch) { setConnected(false); setPhase('idle'); setConnecting(false); } },
        onError: error => { console.error('[连接/语音]', error); if (epoch.current === connectionEpoch) { setError('连接或语音服务出现问题，请重新连接。'); setPhase('idle'); } },
        onDeviceError: error => {
          console.error('[媒体设备]', error);
          if (epoch.current !== connectionEpoch) return;
          setError('麦克风不可用，你可以继续输入文字。');
          release();
        },
        onTrackStarted: (track, participant) => {
          if (track.kind === 'audio' && participant?.local && !holding.current) value.enableMic(false);
        },
      },
    });
    current.current = value; setClient(value);
    try {
      const started = await value.startBot({ endpoint: '/api/start', requestData: { mode } }) as {
        session_id: string; webrtc_request_params: { endpoint: string; headers: Record<string, string> };
      };
      sessionId.current = started.session_id;
      // JSON cannot carry a Headers instance, which this pinned SDK requires.
      await value.connect({ webrtcRequestParams: { ...started.webrtc_request_params,
        headers: new Headers(started.webrtc_request_params.headers) } });
      if (connectionEpoch !== epoch.current) { await value.disconnect(); return; }
      setConnected(true);
    } catch (error) {
      console.error('[连接]', error);
      setError('连接失败，请确认后端已启动后重试。');
      await value.disconnect();
    } finally { if (connectionEpoch === epoch.current) setConnecting(false); }
  }

  function begin(kind: 'text' | 'voice') {
    for (const resolve of accepted.current.values()) resolve(false);
    accepted.current.clear();
    const next = ++generation.current;
    if (audio.current) audio.current.muted = true; // Keep consuming RTP while muted.
    void audio.current?.play().catch(onAudioBlocked);
    newAudio.current = 0;
    clearTimeout(cleanupDeadline.current);
    cleanupDeadline.current = setTimeout(() => {
      if (cleared.current !== next) { setError('打断确认超时，请重新连接。'); void disconnect(); }
    }, 5000);
    setVisibleGeneration(next); setSubtitle(''); setHeard(''); setError(''); setMotion('none'); setPreview(false); setScene(confirmed.current);
    setPhase(kind === 'voice' ? 'listening' : 'thinking');
    const data = { generation: next, kind, mock_text: preset, fault };
    setFault('none');
    return data;
  }

  async function send(value = text) {
    if (!connected || !value.trim() || holding.current) return;
    const data = begin('text');
    const ready = new Promise<boolean>(resolve => accepted.current.set(data.generation, resolve));
    current.current?.sendClientMessage('interaction.begin', data);
    setText('');
    // Native custom messages and send-text have separate processing paths.
    if (!await ready || data.generation !== generation.current) return;
    try { await current.current?.sendText(value.trim(), { run_immediately: true, audio_response: true }); }
    catch (error) { console.error('[文字发送]', error); setError('发送失败，请重新连接。'); setPhase('idle'); }
  }

  async function press() {
    if (!connected || holding.current) return;
    holding.current = true;
    const data = begin('voice');
    current.current?.sendClientMessage('push_to_talk', { ...data, state: 'start' });
    deadline.current = setTimeout(release, 15000);
    try {
      if (mode === 'live') {
        current.current?.enableMic(true);
        // SDK owns device acquisition; permission failures are surfaced through callbacks.
      }
    } catch (error) { console.error('[麦克风]', error); setError('麦克风不可用，你可以继续输入文字。'); release(); }
  }

  function release() {
    if (!holding.current) return;
    holding.current = false;
    clearTimeout(deadline.current);
    current.current?.enableMic(false);
    current.current?.sendClientMessage('push_to_talk', { state: 'stop', generation: generation.current });
    setPhase('thinking');
  }

  async function disconnect() {
    release();
    epoch.current++;
    for (const resolve of accepted.current.values()) resolve(false);
    accepted.current.clear();
    clearTimeout(cleanupDeadline.current);
    if (audio.current) audio.current.muted = true;
    await current.current?.disconnect();
    current.current = null; setClient(null); setConnected(false); setPhase('idle'); setMotion('none'); setPreview(false);
  }

  const photo = scene.photo_shown || preview;
  return <main className="cafe-app">
    <header className="topbar"><a className="brand" href="/">雨夜咖啡馆<span>AFTER THE RAIN</span></a><div className="connection"><i className={connected ? 'online' : ''} />{connected ? '已连接' : connecting ? '连接中' : '尚未入座'}</div></header>
    <div className="experience">
      <section className="story-card" aria-label="角色互动舞台">
        <Stage mood={mood} phase={phase} motion={motion} photo={photo} warm={photo} generation={visibleGeneration} />
        <div className="character-heading"><div><span className="eyebrow">A LITTLE STORY, WITH YOU</span><h1>米拉 <small>Mira</small></h1></div><span className={`phase phase-${phase}`} role="status">{phaseName[phase]}</span></div>
        <div className="subtitle" aria-live="polite" data-testid="subtitle">{subtitle || (connected ? '雨声渐轻。她望向你，等你说点什么。' : '雨停了。咖啡还温着，故事也还没结束。')}</div>
        {heard && <p className="heard">{mode === 'mock' ? '模拟转写' : '你说'}：{heard}</p>}
        {scene.ending_choice && <div className="ending">{scene.ending_choice === 'stay' ? '今晚，等待有了陪伴。' : '有些约定，值得再问候一次。'}</div>}
      </section>
      <aside className="conversation">
        <div className="chapter"><span className="eyebrow">01 / 雨停之后</span><h2>有些等待，<br />适合慢慢聊。</h2><p>一位摄影师，一张旧照片。<br />坐下来，陪她度过这个雨夜。</p></div>
        {!connected ? <div className="entry"><label>体验模式<select value={mode} onChange={e => setMode(e.target.value as 'mock' | 'live')} disabled={connecting}><option value="mock">Mock · 无需密钥</option><option value="live" disabled={!liveAvailable}>Live · 真实语音模型{!liveAvailable ? '（待配置）' : ''}</option></select></label><p className="hint">{mode === 'mock' ? 'Mock 有真实语音播放；按住说话使用所选的模拟转写。' : '按住说话，松开后发送。也可以直接输入文字。'}</p><button className="primary enter" onClick={connect} disabled={connecting}>{connecting ? '正在为你留座…' : '坐下来聊聊 ↗'}</button></div> : <>
          <div className="suggestions">{['你在等谁？', '可以看看那张照片吗？', '我陪你再等一会儿。'].map(v => <button key={v} onClick={() => void send(v)} disabled={phase === 'listening'}>{v}</button>)}</div>
          <form onSubmit={e => { e.preventDefault(); void send(); }}><label htmlFor="message" className="eyebrow">说点什么</label><div className="input-row"><input id="message" value={text} maxLength={500} onChange={e => setText(e.target.value)} placeholder="问问她的故事…" autoComplete="off" disabled={phase === 'listening'} /><button type="submit" className="send" aria-label="发送" disabled={!text.trim() || phase === 'listening'}>↗</button></div></form>
          {mode === 'mock' && <label className="mock-select">模拟转写内容<select value={preset} onChange={e => setPreset(e.target.value)}>{presets.map(v => <option key={v}>{v}</option>)}</select></label>}
          <button className={`push-to-talk ${phase === 'listening' ? 'active' : ''}`} aria-label="按住说话" onPointerDown={e => { e.currentTarget.setPointerCapture(e.pointerId); void press(); }} onPointerUp={release} onPointerCancel={release} onLostPointerCapture={release} onKeyDown={e => { if (e.code === 'Space' && !e.repeat) { e.preventDefault(); void press(); } }} onKeyUp={e => { if (e.code === 'Space') { e.preventDefault(); release(); } }} onBlur={release}>◉ <span>{phase === 'listening' ? '松开发送' : '按住说话'}</span><small>{mode === 'mock' ? '模拟识别' : '最长 15 秒'}</small></button>
          <p className="hint">她说话时，发文字或按住说话即可打断。</p>
          <button className="text-button" onClick={() => void disconnect()}>离开座位</button>
        </>}
        {error && <div className="error" role="alert">{error}</div>}
        {blocked && <button className="audio-unlock" onClick={() => void audio.current?.play().then(() => setBlocked(false)).catch(() => setError('请检查浏览器声音权限。'))}>点击开启声音</button>}
      </aside>
    </div>
    <footer><span>把一场雨，聊成一个故事。</span><button className="text-button" onClick={() => setDebug(!debug)}>调试</button></footer>
    {debug && <div className="debug"><span>会话代次：{visibleGeneration} · {mode} · {phase}</span><label>下一次输入注入故障<select value={fault} disabled={mode !== 'mock'} onChange={e => setFault(e.target.value)}><option value="none">无</option><option value="model_timeout">模型超时</option><option value="tts_failure">TTS 失败</option><option value="asset_failure">照片加载失败</option><option value="old_result_late">旧结果延迟返回</option></select></label><p>每次故障只对下一轮生效。Mock 的转写来自所选文本。</p></div>}
    {client && <PipecatClientProvider client={client}><BotAudio audio={audio} onBlocked={onAudioBlocked} /></PipecatClientProvider>}
  </main>;
}
