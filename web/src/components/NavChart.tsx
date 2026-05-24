// NAV chart — strategy vs baselines. Dark theme matching the app shell.
// Direct wrapper over plotly.js-dist-min (react-plotly.js doesn't support React 19).

import { useEffect, useRef } from "react";
import Plotly from "plotly.js-basic-dist-min";
import type { Data, Layout } from "plotly.js";

interface NavSeries {
  label: string;
  index: string[];
  values: number[];
  color?: string;
  dash?: "solid" | "dash" | "dot";
}

interface NavChartProps {
  series: NavSeries[];
  /** y-axis scale: linear (default) shows $ values, log smooths exponentials */
  logScale?: boolean;
  height?: number;
}

const ACCENT = "#7c5cff";
const POS = "#2dd4bf";
const NEG = "#ef4444";
const DIM = "#9aa0b0";
const SURFACE = "#14171f";
const BORDER = "#2a2f40";

export function NavChart({ series, logScale = false, height = 420 }: NavChartProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const el = ref.current;

    const traces: Data[] = series.map((s, i) => ({
      type: "scatter",
      mode: "lines",
      name: s.label,
      x: s.index,
      y: s.values,
      line: {
        color: s.color ?? (i === 0 ? ACCENT : DIM),
        width: i === 0 ? 2.2 : 1.4,
        dash: s.dash ?? "solid",
      },
      hovertemplate: "%{x|%b %d %Y}<br><b>$%{y:,.0f}</b><extra>%{fullData.name}</extra>",
    }));

    const layout: Partial<Layout> = {
      autosize: true,
      height,
      margin: { l: 56, r: 12, t: 12, b: 36 },
      paper_bgcolor: SURFACE,
      plot_bgcolor: SURFACE,
      showlegend: true,
      legend: {
        orientation: "h",
        x: 0,
        y: 1.05,
        yanchor: "bottom",
        bgcolor: "rgba(0,0,0,0)",
        font: { color: DIM, size: 12 },
      },
      xaxis: {
        type: "date",
        gridcolor: BORDER,
        linecolor: BORDER,
        tickcolor: BORDER,
        tickfont: { color: DIM, size: 11 },
        showgrid: false,
      },
      yaxis: {
        type: logScale ? "log" : "linear",
        gridcolor: BORDER,
        linecolor: BORDER,
        tickcolor: BORDER,
        tickfont: { color: DIM, size: 11 },
        tickformat: ",.0f",
        tickprefix: "$",
        zeroline: false,
      },
      hovermode: "x unified",
      hoverlabel: {
        bgcolor: "#0b0d12",
        bordercolor: BORDER,
        font: { color: "#e7e9ee", size: 12 },
      },
    };

    void Plotly.newPlot(el, traces, layout, {
      displaylogo: false,
      modeBarButtonsToRemove: ["lasso2d", "select2d"],
      responsive: true,
    });

    const onResize = () => {
      void Plotly.Plots.resize(el);
    };
    window.addEventListener("resize", onResize);

    return () => {
      window.removeEventListener("resize", onResize);
      Plotly.purge(el);
    };
  }, [series, logScale, height]);

  return <div ref={ref} style={{ width: "100%", height: `${height}px` }} />;
}

export const COLORS = { ACCENT, POS, NEG, DIM };
