export default function PlayerSearch({ value, onChange, placeholder }) {
  return (
    <div className="relative mb-3">
      <input
        type="text"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder ?? 'Sök namn eller tröjnummer'}
        className="w-full rounded-xl border-2 border-gray-300 pl-3 pr-9 py-2 text-sm
                   focus:outline-none focus:border-black focus:ring-2 focus:ring-black"
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange('')}
          aria-label="Rensa sökning"
          className="absolute right-1.5 top-1/2 -translate-y-1/2 h-6 w-6 flex items-center
                     justify-center rounded-full text-gray-400 hover:bg-gray-100
                     hover:text-gray-600 transition"
        >
          ×
        </button>
      )}
    </div>
  )
}
