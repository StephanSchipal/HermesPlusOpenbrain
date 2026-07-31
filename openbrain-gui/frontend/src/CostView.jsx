import { useState } from 'react'
import ExternalCostGrid from './ExternalCostGrid'

const RANGES = [7, 30, 90]

export default function CostView() {
  const [days, setDays] = useState(30)
  const [externalTotals, setExternalTotals] = useState(null)

  return (
    <div className="cost-view">
      <div className="cost-range">
        {RANGES.map((d) => (
          <button key={d} className={days === d ? 'active' : ''} onClick={() => setDays(d)}>
            {d} days
          </button>
        ))}
      </div>

      {externalTotals && (
        <p className="cost-external-total">
          External recurring: ${externalTotals.monthly_usd.toFixed(2)}/month
          {externalTotals.onetime_usd > 0 &&
            ` · one-off: $${externalTotals.onetime_usd.toFixed(2)}`}
          {/* A EUR row with no FX rate contributes 0 to the sums above, so the
              figure is understated. Say so rather than show it bare. */}
          {externalTotals.incomplete && (
            <span className="cost-warning"> — incomplete, a euro row needs an exchange rate</span>
          )}
        </p>
      )}

      <ExternalCostGrid onTotalsChange={setExternalTotals} />
    </div>
  )
}
