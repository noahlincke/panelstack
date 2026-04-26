import type { ReadingPath } from '../api/types';
import avengersIcon from '../assets/filter-icons/avengers.jpg';
import batmanIcon from '../assets/filter-icons/batman.jpg';
import berserkIcon from '../assets/filter-icons/berserk.svg';
import chainsawManIcon from '../assets/filter-icons/chainsaw-man.svg';
import daredevilIcon from '../assets/filter-icons/daredevil.svg';
import fantasticFourIcon from '../assets/filter-icons/fantastic-four.jpg';
import flashIcon from '../assets/filter-icons/flash.svg';
import frierenIcon from '../assets/filter-icons/frieren.svg';
import greenLanternIcon from '../assets/filter-icons/green-lantern.svg';
import hunterXHunterIcon from '../assets/filter-icons/hunter-x-hunter.jpg';
import justiceLeagueIcon from '../assets/filter-icons/justice-league.jpg';
import jjkIcon from '../assets/filter-icons/jjk.jpg';
import jojoIcon from '../assets/filter-icons/jojo.jpg';
import onePieceIcon from '../assets/filter-icons/one-piece.svg';
import spiderManIcon from '../assets/filter-icons/spider-man.jpg';
import spyXFamilyIcon from '../assets/filter-icons/spy-x-family.svg';
import supermanIcon from '../assets/filter-icons/superman.jpg';
import vinlandSagaIcon from '../assets/filter-icons/vinland-saga.svg';
import wolverineIcon from '../assets/filter-icons/wolverine.jpg';
import wonderWomanIcon from '../assets/filter-icons/wonder-woman.jpg';
import xMenIcon from '../assets/filter-icons/x-men.jpg';

export type CatalogFilterItem = {
  id: string;
  label: string;
  matchTerms: string[];
  imageSrc: string;
};

export type CatalogFilterGroup = {
  id: 'dc' | 'marvel' | 'anime';
  publisher: string;
  label: string;
  items: CatalogFilterItem[];
};

export const FILTER_GROUPS: CatalogFilterGroup[] = [
  {
    id: 'dc',
    publisher: 'DC Comics',
    label: 'DC',
    items: [
      {
        id: 'batman-family',
        label: 'Batman Family',
        matchTerms: [
          'batman',
          'detective comics',
          'nightwing',
          'robin',
          'batgirl',
          'batwoman',
          'red hood',
          'red robin',
          'catwoman',
          'gotham',
          'azrael',
          'outsiders',
          'harley quinn',
          'poison ivy',
          'birds of prey',
        ],
        imageSrc: batmanIcon,
      },
      {
        id: 'superman-family',
        label: 'Superman Family',
        matchTerms: [
          'superman',
          'action comics',
          'supergirl',
          'superboy',
          'power girl',
          'steel',
          'krypto',
          'super sons',
          'krypton',
        ],
        imageSrc: supermanIcon,
      },
      {
        id: 'wonder-woman-family',
        label: 'Wonder Woman Family',
        matchTerms: [
          'wonder woman',
          'diana prince',
          'amazon',
          'amazons',
          'nubia',
          'wonder girl',
          'yara flor',
          'trinity',
        ],
        imageSrc: wonderWomanIcon,
      },
      {
        id: 'lantern-family',
        label: 'Lanterns',
        matchTerms: [
          'green lantern',
          'green lantern corps',
          'lantern',
          'lanterns',
          'war journal',
          'hal jordan',
          'john stewart',
          'jo mullein',
          'guy gardner',
          'kyle rayner',
          'sinestro',
          'oa',
        ],
        imageSrc: greenLanternIcon,
      },
      {
        id: 'flash-family',
        label: 'Flash Family',
        matchTerms: [
          'flash',
          'speed force',
          'wally west',
          'barry allen',
          'jay garrick',
          'reverse flash',
          'rogues',
          'impulse',
          'kid flash',
          'max mercury',
        ],
        imageSrc: flashIcon,
      },
      {
        id: 'justice-league-family',
        label: 'Justice League and DC Teams',
        matchTerms: [
          'justice league',
          'justice society',
          'jla',
          'justice league dark',
          'titans',
          'teen titans',
          'green lantern',
          'flash',
          'aquaman',
          'green arrow',
          'suicide squad',
          'hawkman',
          'hawkgirl',
          'blue beetle',
          'booster gold',
          'shazam',
        ],
        imageSrc: justiceLeagueIcon,
      },
    ],
  },
  {
    id: 'marvel',
    publisher: 'Marvel',
    label: 'Marvel',
    items: [
      {
        id: 'spider-family',
        label: 'Spider-Verse',
        matchTerms: [
          'spider man',
          'spider-man',
          'spiderverse',
          'spider verse',
          'spider-verse',
          'spider gwen',
          'ghost spider',
          'spider woman',
          'scarlet spider',
          'miles morales',
          'peter parker',
          'silk',
          'venom',
          'carnage',
          'spider',
        ],
        imageSrc: spiderManIcon,
      },
      {
        id: 'avengers-family',
        label: 'Avengers',
        matchTerms: [
          'avengers',
          'new avengers',
          'west coast avengers',
          'young avengers',
          'ultimates',
          'illuminati',
          'iron man',
          'captain america',
          'captain marvel',
          'thor',
          'black panther',
          'hulk',
          'she hulk',
          'doctor strange',
          'ant man',
          'wasp',
        ],
        imageSrc: avengersIcon,
      },
      {
        id: 'x-men-family',
        label: 'X-Men',
        matchTerms: [
          'x men',
          'x-men',
          'uncanny',
          'nyx',
          'phoenix',
          'new mutants',
          'x factor',
          'x-force',
          'x force',
          'weapon x',
          'excalibur',
          'academy x',
          'kamala khan',
          'ms marvel',
          'hellion',
          'prodigy',
          'anole',
          'sophie cuckoo',
          'mutant',
          'cable',
          'cyclops',
          'jean grey',
          'storm',
          'nightcrawler',
          'gambit',
          'rogue',
          'magneto',
        ],
        imageSrc: xMenIcon,
      },
      {
        id: 'fantastic-four-family',
        label: 'Fantastic Four',
        matchTerms: [
          'fantastic four',
          'future foundation',
          'mr fantastic',
          'reed richards',
          'invisible woman',
          'sue storm',
          'human torch',
          'johnny storm',
          'thing',
          'ben grimm',
        ],
        imageSrc: fantasticFourIcon,
      },
      {
        id: 'wolverine-family',
        label: 'Wolverine',
        matchTerms: [
          'wolverine',
          'logan',
          'x-23',
          'x 23',
          'laura kinney',
          'all-new wolverine',
          'daken',
          'sabretooth',
        ],
        imageSrc: wolverineIcon,
      },
      {
        id: 'street-level-family',
        label: 'Street-Level',
        matchTerms: [
          'daredevil',
          'hell s kitchen',
          'hells kitchen',
          'elektra',
          'punisher',
          'defenders',
          'echo',
          'street level',
        ],
        imageSrc: daredevilIcon,
      },
    ],
  },
  {
    id: 'anime',
    publisher: 'Anime',
    label: 'Anime',
    items: [
      {
        id: 'jujutsu-kaisen',
        label: 'Jujutsu Kaisen',
        matchTerms: [
          'jujutsu kaisen',
          'jjk',
          'sorcery fight',
        ],
        imageSrc: jjkIcon,
      },
      {
        id: 'jojo-family',
        label: 'JoJo',
        matchTerms: [
          'jojo',
          'jojo s bizarre adventure',
          'jojo no kimyou na bouken',
          'phantom blood',
          'battle tendency',
          'stardust crusaders',
          'diamond is unbreakable',
          'golden wind',
          'ougon no kaze',
          'stone ocean',
          'steel ball run',
          'jojolion',
          'jojolands',
        ],
        imageSrc: jojoIcon,
      },
      {
        id: 'hunter-x-hunter-family',
        label: 'Hunter x Hunter',
        matchTerms: [
          'hunter x hunter',
          'hunterxhunter',
          'gon freecss',
          'killua',
          'kurapika',
          'leorio',
          'hisoka',
          'phantom troupe',
          'chimera ant',
          'greed island',
        ],
        imageSrc: hunterXHunterIcon,
      },
      {
        id: 'berserk-family',
        label: 'Berserk',
        matchTerms: [
          'berserk',
          'guts',
          'griffith',
          'band of the hawk',
          'black swordsman',
        ],
        imageSrc: berserkIcon,
      },
      {
        id: 'chainsaw-man-family',
        label: 'Chainsaw Man',
        matchTerms: [
          'chainsaw man',
          'denji',
          'makima',
          'asa mitaka',
          'pochita',
          'public safety',
        ],
        imageSrc: chainsawManIcon,
      },
      {
        id: 'one-piece-family',
        label: 'One Piece',
        matchTerms: [
          'one piece',
          'luffy',
          'straw hat',
          'mugiwara',
          'grand line',
          'egghead',
        ],
        imageSrc: onePieceIcon,
      },
      {
        id: 'spy-x-family-family',
        label: 'Spy x Family',
        matchTerms: [
          'spy x family',
          'spy family',
          'anya',
          'loid',
          'yor',
          'forger',
        ],
        imageSrc: spyXFamilyIcon,
      },
      {
        id: 'frieren-family',
        label: 'Frieren',
        matchTerms: [
          'frieren',
          'sousou no frieren',
          'beyond journey s end',
          'beyond journeys end',
          'fern',
          'stark',
        ],
        imageSrc: frierenIcon,
      },
      {
        id: 'vinland-saga-family',
        label: 'Vinland Saga',
        matchTerms: [
          'vinland saga',
          'thorfinn',
          'askeladd',
          'canute',
          'vinland',
        ],
        imageSrc: vinlandSagaIcon,
      },
    ],
  },
];

const FILTERS_BY_ID = new Map(FILTER_GROUPS.flatMap((group) => group.items.map((item) => [item.id, item])));

function normalizeSearchValue(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

export function matchesCatalogFilters(path: ReadingPath, activeFilterIds: string[]): boolean {
  if (activeFilterIds.length === 0) {
    return true;
  }

  const haystack = normalizeSearchValue(`${path.title} ${path.slug ?? ''} ${(path.tags ?? []).join(' ')}`);
  return activeFilterIds.some((filterId) => {
    const filter = FILTERS_BY_ID.get(filterId);
    return filter
      ? filter.matchTerms.some((term) => haystack.includes(normalizeSearchValue(term)))
      : false;
  });
}

export function matchesAnyCatalogFilter(path: ReadingPath): boolean {
  return matchesCatalogFilters(path, Array.from(FILTERS_BY_ID.keys()));
}
