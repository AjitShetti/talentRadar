'use client'

import { useEffect, useRef } from 'react'
import { motionPaused, onMotionChange } from './motion'

/**
 * The ground of the hero and the closing CTA: hundreds of fine ribs, each running
 * the blend from top to bottom, standing in an arch that is tallest in the middle.
 * The arch expands outward from the centre when it first comes into view, then
 * keeps slowly widening and settling.
 *
 * Under the hero the ribs carry on past the first screen: each runs back through
 * azure into black at its own length, and reaches further as the page scrolls, so
 * the prism trails off into the page instead of stopping at an edge.
 *
 * With `across`, as on the sign-in pages, the same arch lies on its side: the
 * ribs run left to right, from black behind the words to salmon behind the form.
 *
 * One fragment shader on one canvas, with no library. The section's own CSS
 * gradient is what shows without WebGL, so nothing depends on this running, and
 * the loop stops whenever the section is off screen or the tab is hidden.
 */

const VERTEX = 'attribute vec2 a;void main(){gl_Position=vec4(a,0.,1.);}'
const FRAGMENT = `
precision highp float;
uniform vec2 r;uniform float T;uniform float cw;uniform float azureAt;uniform float h;uniform float fall;uniform float across;
const float S0=.167;const float S1=.572;const float S2=.906;
const float FIRST=.18;
const vec3 C0=vec3(0.);
const vec3 C1=vec3(44.,117.,255.)/255.;
const vec3 C2=vec3(255.,160.,122.)/255.;
vec3 ramp(float t){
  if(t<S1)return mix(C0,C1,clamp((t-S0)/(S1-S0),0.,1.));
  return mix(C1,C2,clamp((t-S1)/(S2-S1),0.,1.));
}
float rnd(float i){return fract(sin(i*91.7+3.1)*437.5453);}
float wander(float x){float i=floor(x);float f=fract(x);f=f*f*(3.-2.*f);return mix(rnd(i),rnd(i+1.),f);}
float hash(vec2 p){return fract(sin(dot(p,vec2(12.9898,78.233)))*43758.5453);}
void main(){
  vec2 R=across>.5?r.yx:r;
  vec2 P=across>.5?vec2(gl_FragCoord.y,r.x-gl_FragCoord.x):gl_FragCoord.xy;
  float y=(R.y-P.y)/h;
  float idx=floor(P.x/cw);
  float n=abs((idx+.5)*cw-R.x*.5)/(R.x*.5);
  float a=rnd(idx);
  float span=(azureAt-FIRST)/(S1-S0);
  float breath=1.+.08*(.5+.5*sin(T*.8));
  float contour=(wander(idx*.035+T*.06)-.5)*.07;
  float streak=pow(a,14.)*.1;
  float shimmer=.008*sin(T*2.+a*6.283)+.006*sin(T*2.-n*6.);
  float y0=FIRST-S0*span+.26*pow(n/breath,1.6)+contour-streak+(a-.5)*.012+shimmer;
  float e=1.-clamp((T-n*.35)/.9,0.,1.);
  y0+=e*e*e*1.05;
  float t=(y-y0)/span;
  float back=0.;
  float tail=R.y/h-1.;
  float start=1.-.1*rnd(idx+11.);
  if(tail>.001&&y>start){
    float reach=clamp((1.-n*n*n*n)*.55+pow(rnd(idx+37.),1.6)*.6,.3,1.);
    back=clamp((y-start)/(tail*reach*(.62+.38*fall)),0.,1.);
    t=min(t,S2)-back*(S2-S0);
  }
  vec3 col=ramp(t);
  float flute=abs(2.*fract(P.x/cw)-1.);
  col*=(1.-.14*flute*flute)*(.95+.1*a);
  col+=(hash(P)-.5)*.04*(1.-back);
  gl_FragColor=vec4(col,1.);
}`

// The blend's speed, as a fraction of its own 0-100 scale.
const SPEED = 40 / 100
// Where a reduced-motion viewer sees the arch: expanded and still.
const SETTLED_AT = 2
const RIBS_ACROSS = 240
// Where azure lands on its side, as a fraction of the width: past the left panel's words.
const AZURE_AT_ACROSS = .55

export default function PrismGround({ across = false, className = 'lp-prism' }: { across?: boolean; className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const section = canvas?.parentElement
    if (!canvas || !section) return
    const gl = canvas.getContext('webgl', { antialias: false, alpha: false })
    if (!gl) { canvas.hidden = true; return }

    const compile = (type: number, source: string) => {
      const shader = gl.createShader(type) as WebGLShader
      gl.shaderSource(shader, source)
      gl.compileShader(shader)
      return shader
    }
    const program = gl.createProgram() as WebGLProgram
    const vertex = compile(gl.VERTEX_SHADER, VERTEX)
    const fragment = compile(gl.FRAGMENT_SHADER, FRAGMENT)
    gl.attachShader(program, vertex)
    gl.attachShader(program, fragment)
    gl.linkProgram(program)
    // An opaque canvas that never draws would hide the gradient, so it steps aside.
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) { canvas.hidden = true; return }
    gl.useProgram(program)

    const buffer = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)
    const position = gl.getAttribLocation(program, 'a')
    gl.enableVertexAttribArray(position)
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0)

    const uR = gl.getUniformLocation(program, 'r')
    const uT = gl.getUniformLocation(program, 'T')
    const uCw = gl.getUniformLocation(program, 'cw')
    const uAzureAt = gl.getUniformLocation(program, 'azureAt')
    const uH = gl.getUniformLocation(program, 'h')
    const uFall = gl.getUniformLocation(program, 'fall')
    gl.uniform1f(gl.getUniformLocation(program, 'across'), across ? 1 : 0)

    // The blend is laid out over the first screen; anything below that is the tail.
    const firstScreen = section.querySelector('.lp-hero') ?? section

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let paused = motionPaused() || reduced
    let lost = false
    let elapsed = reduced ? SETTLED_AT : 0
    let last = 0
    let ribWidth = 5
    let azureAt = across ? AZURE_AT_ACROSS : .5
    let firstHeight = 1
    let visible = true
    let frame = 0

    const draw = () => {
      gl.uniform2f(uR, canvas.width, canvas.height)
      gl.uniform1f(uT, elapsed)
      gl.uniform1f(uCw, ribWidth)
      gl.uniform1f(uAzureAt, azureAt)
      gl.uniform1f(uH, firstHeight)
      gl.uniform1f(uFall, reduced ? 1 : Math.min(window.scrollY / firstScreen.clientHeight, 1))
      gl.drawArrays(gl.TRIANGLES, 0, 3)
    }
    const fit = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5)
      canvas.width = Math.max(1, Math.round(section.clientWidth * dpr))
      canvas.height = Math.max(1, Math.round(section.clientHeight * dpr))
      ribWidth = Math.max(Math.round(section.clientWidth / RIBS_ACROSS * dpr), 4)
      firstHeight = across ? canvas.width : Math.max(1, firstScreen.clientHeight * dpr)
      // Azure lands behind the Try button at the centre, wherever the layout puts it.
      const button = section.querySelector('.lp-try')?.getBoundingClientRect()
      if (button) azureAt = (button.top + button.height / 2 - section.getBoundingClientRect().top) / firstScreen.clientHeight
      gl.viewport(0, 0, canvas.width, canvas.height)
      draw()
    }
    const tick = (now: number) => {
      frame = 0
      if (paused || lost || !visible || document.hidden) { last = 0; return }
      if (last) elapsed += Math.min((now - last) / 1000, .1) * SPEED
      last = now
      draw()
      frame = requestAnimationFrame(tick)
    }
    const run = () => { if (!frame && !paused && !lost) frame = requestAnimationFrame(tick) }

    const offMotion = onMotionChange(next => { paused = next; run() })
    const intersection = new IntersectionObserver(entries => { visible = entries[entries.length - 1].isIntersecting; run() })
    intersection.observe(section)
    const resize = new ResizeObserver(fit)
    resize.observe(section)
    document.addEventListener('visibilitychange', run)
    // Phones reclaim contexts from background tabs; the gradient takes over.
    const onLost = () => {
      lost = true
      cancelAnimationFrame(frame)
      frame = 0
      canvas.hidden = true
    }
    canvas.addEventListener('webglcontextlost', onLost)

    fit()
    run()
    return () => {
      cancelAnimationFrame(frame)
      offMotion()
      intersection.disconnect()
      resize.disconnect()
      document.removeEventListener('visibilitychange', run)
      canvas.removeEventListener('webglcontextlost', onLost)
      gl.deleteProgram(program)
      gl.deleteShader(vertex)
      gl.deleteShader(fragment)
      gl.deleteBuffer(buffer)
    }
  }, [across])

  return <canvas ref={canvasRef} className={className} aria-hidden="true" />
}
