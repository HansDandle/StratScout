// Onboarding / template picker. The user's first screen.
// "Pick a starting strategy, hit Run, see what it does on history."

import { TEMPLATES } from "../templates";
import { useApp } from "../store";
import { Card, RiskBadge } from "../components/ui";

export function Onboarding() {
  const setTemplate = useApp((s) => s.setTemplate);

  return (
    <div className="max-w-5xl mx-auto p-8">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold mb-2">Pick a starting strategy</h1>
        <p className="text-(--color-text-dim) max-w-xl">
          These are templates — solid starting points you can tune later. Every backtest
          shows you against SPY buy-and-hold so you know if it's actually better than just
          holding the index.
        </p>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {TEMPLATES.map((t) => (
          <button
            key={t.id}
            onClick={() => setTemplate(t)}
            className="text-left group"
          >
            <Card className="p-5 h-full hover:border-(--color-accent) transition cursor-pointer">
              <div className="flex items-start justify-between mb-3">
                <h2 className="text-lg font-semibold">{t.name}</h2>
                <RiskBadge level={t.riskLabel} />
              </div>
              <p className="text-sm text-(--color-text-dim) leading-relaxed">
                {t.description}
              </p>
              <div className="mt-4 text-xs text-(--color-accent) opacity-0 group-hover:opacity-100 transition">
                Select →
              </div>
            </Card>
          </button>
        ))}
      </div>

      <p className="text-xs text-(--color-text-dim) mt-8 max-w-xl">
        After picking a template you'll see backtest results immediately. Nothing is
        traded until you explicitly switch a strategy to Paper or Live.
      </p>
    </div>
  );
}
