import { useEffect, useRef, useState } from 'react';
import { AnimatedSprite, Application, Assets, Spritesheet } from 'pixi.js';

export type Mood = 'guarded' | 'curious' | 'warm';
export type Phase = 'idle' | 'listening' | 'thinking' | 'speaking';
export type Motion = 'none' | 'look_window' | 'hold_photo';

export function Stage({ mood, phase, motion, photo, warm, generation }: {
  mood: Mood; phase: Phase; motion: Motion; photo: boolean; warm: boolean; generation: number;
}) {
  const host = useRef<HTMLDivElement>(null);
  const sprite = useRef<AnimatedSprite | null>(null);
  const sheet = useRef<Spritesheet | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const element = host.current!;
    const app = new Application();
    let disposed = false;
    let resize: ResizeObserver | undefined;
    (async () => {
      await app.init({ backgroundAlpha: 0, antialias: true, resolution: Math.min(devicePixelRatio, 2), autoDensity: true, resizeTo: element });
      if (disposed) { app.destroy(true); return; }
      element.appendChild(app.canvas);
      sheet.current = await Assets.load<Spritesheet>('/assets/mira.json');
      if (disposed) { app.destroy(true); return; }
      const actor = new AnimatedSprite(sheet.current.animations['guarded-idle']);
      sprite.current = actor;
      actor.anchor.set(.5, 1);
      app.stage.addChild(actor);
      const fit = () => {
        actor.scale.set(Math.min(element.clientHeight / 470, element.clientWidth / 350));
        actor.position.set(element.clientWidth * .53, element.clientHeight * .97);
      };
      resize = new ResizeObserver(fit);
      resize.observe(element);
      fit();
      setLoaded(true);
    })().catch(() => { if (!disposed) setFailed(true); });
    return () => {
      disposed = true;
      resize?.disconnect();
      sprite.current = null;
      if (app.renderer) app.destroy(true, { children: true });
    };
  }, []);
  useEffect(() => {
    const actor = sprite.current;
    const atlas = sheet.current;
    if (!actor || !atlas || !loaded) return;
    actor.stop();
    actor.onComplete = undefined;
    const action = motion === 'none' ? phase : motion;
    actor.textures = atlas.animations[`${mood}-${action}`];
    actor.animationSpeed = action === 'speaking' ? .12 : motion !== 'none' ? .1 : .025;
    actor.loop = motion === 'none';
    if (!actor.loop) actor.onComplete = () => {
      actor.textures = atlas.animations[`${mood}-${phase}`];
      actor.loop = true;
      actor.animationSpeed = .04;
      actor.play();
    };
    actor.gotoAndPlay(0);
  }, [mood, phase, motion, generation, loaded]);
  return <div className={`stage ${warm ? 'warm' : ''}`} data-phase={phase} data-mood={mood} data-motion={motion}>
    <div className="stage-light" />
    <div className="rain" />
    <div ref={host} className="actor" />
    {failed && <p className="asset-error">角色暂时无法加载，请刷新页面。</p>}
    {photo && <figure className="memory"><img src="/assets/photo.svg" alt="米拉和朋友在海边的旧照片" /><figcaption>我们第一次去海边。</figcaption></figure>}
    <div className="stage-caption"><span>雨停之后</span><span>26 岁 · 旅行摄影师</span></div>
  </div>;
}
