import { useMemo } from 'react';
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { selectCurrentScenario, selectSelectedPressures, useDashboard } from '../store/useDashboard';

const EMPTY_SENSOR_IDS: string[] = [];

function timeLabel(value: number): string {
  return new Date(value).toLocaleTimeString('en-IN', {
    timeZone: 'Asia/Kolkata',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

export function PressureChart() {
  const selectedSensor = useDashboard((state) => state.selectedSensor);
  const points = useDashboard(selectSelectedPressures);
  const anomalyAt = useDashboard((state) => state.anomalyAt);
  const sensors = useDashboard((state) => state.network?.sensors ?? EMPTY_SENSOR_IDS);
  const setSelectedSensor = useDashboard((state) => state.setSelectedSensor);
  const scenario = useDashboard(selectCurrentScenario);
  const data = useMemo(() => points.slice(-90).map((point) => ({
    ...point,
    epoch: Date.parse(point.timestamp),
  })), [points]);
  const anomalyEpoch = anomalyAt ? Date.parse(anomalyAt) : null;
  const firstEpoch = data[0]?.epoch;
  const lastEpoch = data[data.length - 1]?.epoch;
  const anomalyIsEarlier = anomalyEpoch !== null && firstEpoch !== undefined && anomalyEpoch < firstEpoch;
  const anomalyInWindow = anomalyEpoch !== null && firstEpoch !== undefined && lastEpoch !== undefined && anomalyEpoch >= firstEpoch && anomalyEpoch <= lastEpoch;

  return (
    <section className="panel chart-block">
      <div className="chart-heading">
        <div><div className="eyebrow">PRESSURE / 90-POINT WINDOW</div><h3>{selectedSensor ? `SENSOR ${selectedSensor}` : 'NO SENSOR SELECTED'}</h3></div>
        <div className="chart-controls">
          <label className="inline-select"><span>Sensor</span><select value={selectedSensor ?? ''} onChange={(event) => setSelectedSensor(event.target.value)} aria-label="Pressure chart sensor">
            {sensors.map((id) => <option key={id} value={id}>{id}</option>)}
          </select></label>
          <span className="unit-chip">m head</span>
          {anomalyIsEarlier && <span className="anomaly-chip">ANOMALY EARLIER · {timeLabel(anomalyEpoch!)}</span>}
        </div>
      </div>
      <div className="chart-wrap">
        {data.length ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} syncId="pressure" margin={{ top: 16, right: 30, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#2b373b" strokeDasharray="2 5" vertical={false} />
              <XAxis
                dataKey="epoch"
                type="number"
                scale="time"
                domain={['dataMin', 'dataMax']}
                tickFormatter={timeLabel}
                tick={{ fill: '#a1afae', fontSize: 14, fontFamily: 'IBM Plex Mono' }}
                axisLine={{ stroke: '#38464a' }}
                tickLine={false}
                minTickGap={54}
              />
              <YAxis domain={['dataMin - 1.5', 'dataMax + 1.5']} tick={{ fill: '#a1afae', fontSize: 14, fontFamily: 'IBM Plex Mono' }} axisLine={false} tickLine={false} width={50} />
              <Tooltip
                cursor={{ stroke: '#91a6a2', strokeDasharray: '3 4' }}
                contentStyle={{ background: '#11191c', border: '1px solid #485255', color: '#e7efec', borderRadius: 2 }}
                labelStyle={{ color: '#adb8b6', fontFamily: 'IBM Plex Mono' }}
                labelFormatter={(label: number) => `${timeLabel(label)} IST`}
                formatter={(value: number) => [`${value.toFixed(2)} m`, 'Pressure']}
              />
              {scenario && <ReferenceLine y={scenario.basePressureM} stroke="#737d7d" strokeDasharray="5 5" label={{ value: 'BASE', fill: '#a5afad', fontSize: 12, position: 'insideTopLeft' }} />}
              {anomalyInWindow && <ReferenceLine x={anomalyEpoch!} stroke="#edae56" strokeDasharray="4 4" label={{ value: 'ANOMALY', fill: '#edae56', fontSize: 12, position: 'insideTopRight' }} />}
              <Line type="monotone" dataKey="pressureM" name="Pressure" stroke="#50a99d" strokeWidth={2.2} dot={false} activeDot={{ r: 4, fill: '#d8ebe5', stroke: '#50a99d', strokeWidth: 1.5 }} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : <div className="empty-chart">NO PRESSURE SAMPLE · {selectedSensor ?? 'SENSOR —'}</div>}
      </div>
    </section>
  );
}
