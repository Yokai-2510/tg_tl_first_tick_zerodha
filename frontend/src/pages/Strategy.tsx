import ConfigForm from '../components/ConfigForm'

/** The trading knobs: what to select, when to fire, when to get out. */
export default function Strategy() {
  return (
    <ConfigForm
      title="Strategy"
      note="Entry and exit behaviour. Exit thresholds apply immediately; universe and
            instrument selection decide what gets subscribed and need a restart."
      sections={['entry', 'exits', 'universe', 'instruments', 'positions']}
    />
  )
}
