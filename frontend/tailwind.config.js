export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // Klubbfärg – header och accenter. Ljus, mättad orange som håller
        // kontrast mot svart text på en telefonskärm i dålig sporthallsbelysning.
        'tuif-orange': '#FF6A00',
      },
      fontFamily: {
        // Kondenserad versalgemen display-font – bara i klubbheadern, aldrig i
        // brödtext eller knappar.
        header: ['"Bebas Neue"', 'Impact', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
