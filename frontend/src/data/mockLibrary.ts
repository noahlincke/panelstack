import type { Issue, ReadingPath, Series } from '../api/types';

export const series: Series[] = [
  {
    id: 'absolute-batman',
    title: 'Absolute Batman',
    publisher: 'DC',
    yearStarted: 2024,
    status: 'ongoing',
    synopsis:
      'A stripped-down Batman run with a harder silhouette, a slower burn, and a focus on mood, obsession, and the cost of vigilance.',
    accentClass: 'accent-batman',
    tags: ['starter-friendly', 'event-adjacent', 'urban noir'],
    issueCount: 18,
  },
  {
    id: 'immortal-thor',
    title: 'Immortal Thor',
    publisher: 'Marvel',
    yearStarted: 2023,
    status: 'ongoing',
    synopsis:
      'A mythic, dialogue-heavy run that treats thunder like a political and cosmic force, with enough continuity to reward a guided path.',
    accentClass: 'accent-thor',
    tags: ['mythic', 'continuity heavy', 'modern classic'],
    issueCount: 24,
  },
  {
    id: 'parademon-files',
    title: 'The Parademon Files',
    publisher: 'Image',
    yearStarted: 2021,
    status: 'completed',
    synopsis:
      'A dense conspiracy series built around a city-wide occult mystery, suited for readers who want a compact but layered path.',
    accentClass: 'accent-parademon',
    tags: ['compact', 'mystery', 'limited run'],
    issueCount: 12,
  },
];

export const issues: Issue[] = [
  {
    id: 'absolute-batman-17',
    seriesId: 'absolute-batman',
    number: '017',
    title: 'Broken Glass, Quiet Fire',
    releaseDate: '2026-03-12',
    pageCount: 28,
    summary:
      'Batman follows a thread through a collapsed district while the city narrates the story back to him in fragments and reflections.',
    cover: 'A rain-streaked rooftop with a narrow beam of light crossing the skyline.',
    pages: Array.from({ length: 8 }, (_, index) => ({
      index: index + 1,
      title: `Page ${index + 1}`,
      caption:
        index % 2 === 0
          ? 'Quiet panel rhythm, long shadows, and a single hard contrast line.'
          : 'A structural page that sets up the next beat without crowding the frame.',
      tone:
        index % 3 === 0
          ? 'amber'
          : index % 3 === 1
            ? 'slate'
            : 'bone',
    })),
  },
  {
    id: 'immortal-thor-12',
    seriesId: 'immortal-thor',
    number: '012',
    title: 'The Weight of Weather',
    releaseDate: '2025-11-06',
    pageCount: 30,
    summary:
      'A council scene, a storm, and a long argument about what a god owes to the world that worships him.',
    cover: 'A thunderhead breaking over a stone hall with gold lighting in the windows.',
    pages: Array.from({ length: 10 }, (_, index) => ({
      index: index + 1,
      title: `Page ${index + 1}`,
      caption:
        index % 2 === 0
          ? 'Dense captioning and formal composition.'
          : 'Large atmospheric panels with a steady pacing cadence.',
      tone:
        index % 3 === 0
          ? 'storm'
          : index % 3 === 1
            ? 'ink'
            : 'mist',
    })),
  },
];

export const readingPaths: ReadingPath[] = [
  {
    id: 'batman-primer',
    title: 'Batman: A clean entry path',
    description:
      'A compact order that bridges the main continuity beats and keeps the event tie-ins optional until they matter.',
    totalIssues: 18,
    estimate: '6 to 8 hours',
    seriesIds: ['absolute-batman'],
  },
  {
    id: 'thor-mythic-line',
    title: 'Thor: mythic run, no dead ends',
    description:
      'A reading route designed to preserve the emotional arc while cutting away the most redundant crossovers.',
    totalIssues: 24,
    estimate: '8 to 10 hours',
    seriesIds: ['immortal-thor'],
  },
];
