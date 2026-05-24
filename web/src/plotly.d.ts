// plotly.js-dist-min ships only JS, but its runtime API mirrors plotly.js,
// so we just re-export those types under the dist-min module name.
declare module "plotly.js-basic-dist-min" {
  export * from "plotly.js";
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const Plotly: any;
  export default Plotly;
}
