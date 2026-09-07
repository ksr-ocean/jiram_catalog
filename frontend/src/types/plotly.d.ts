declare module 'plotly.js-dist-min' {
  export type PlotData = Record<string, unknown>;
  export type Layout = Record<string, unknown>;
  export type Config = Record<string, unknown>;
  export function newPlot(
    root: HTMLElement,
    data: PlotData[],
    layout?: Layout,
    config?: Config,
  ): Promise<HTMLElement>;
  export function react(
    root: HTMLElement,
    data: PlotData[],
    layout?: Layout,
    config?: Config,
  ): Promise<HTMLElement>;
  export function purge(root: HTMLElement): void;
  export function toImage(root:HTMLElement,options:{format:'svg'|'png';width?:number;height?:number;scale?:number}):Promise<string>;
  export function Plots(): void;
  const _default: {
    newPlot: typeof newPlot;
    react: typeof react;
    purge: typeof purge;
  };
  export default _default;
}
