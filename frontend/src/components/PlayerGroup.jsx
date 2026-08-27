import PlayerRow from './PlayerRow'

const GROUP_STYLES = {
  'must-sit': {
    title: 'text-amber-800',
    container: 'border border-amber-200 bg-amber-50 rounded-xl',
    divider: 'border-amber-200',
  },
  available: {
    title: 'text-gray-700',
    container: 'border border-gray-200 bg-white rounded-xl',
    divider: 'border-gray-100',
  },
  locked: {
    title: 'text-gray-500',
    container: 'border border-gray-200 bg-gray-50 rounded-xl',
    divider: 'border-gray-200',
  },
}

export default function PlayerGroup({ title, players, variant }) {
  const styles = GROUP_STYLES[variant]

  return (
    <div>
      <h2 className={`text-xs font-semibold uppercase tracking-wide mb-2 ${styles.title}`}>
        {title} ({players.length})
      </h2>
      <div className={styles.container}>
        {players.length === 0 ? (
          <p className="px-4 py-3 text-sm text-gray-400">Inga spelare</p>
        ) : (
          players.map((player, i) => (
            <div key={player.player_id}>
              <div className="px-4">
                <PlayerRow player={player} variant={variant} />
              </div>
              {i < players.length - 1 && (
                <div className={`border-t ${styles.divider}`} />
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
