import avengersIcon from '../assets/filter-icons/avengers.jpg';
import batmanIcon from '../assets/filter-icons/batman.jpg';
import daredevilIcon from '../assets/filter-icons/daredevil.svg';
import fantasticFourIcon from '../assets/filter-icons/fantastic-four.jpg';
import flashIcon from '../assets/filter-icons/flash.svg';
import greenLanternIcon from '../assets/filter-icons/green-lantern.svg';
import hunterXHunterIcon from '../assets/filter-icons/hunter-x-hunter.jpg';
import ironManIcon from '../assets/filter-icons/iron-man.png';
import justiceLeagueIcon from '../assets/filter-icons/justice-league.jpg';
import jjkIcon from '../assets/filter-icons/jjk.jpg';
import jojoIcon from '../assets/filter-icons/jojo.jpg';
import onePieceIcon from '../assets/filter-icons/one-piece.png';
import spiderManIcon from '../assets/filter-icons/spider-man.jpg';
import supermanIcon from '../assets/filter-icons/superman.jpg';
import thorIcon from '../assets/filter-icons/thor.png';
import wolverineIcon from '../assets/filter-icons/wolverine.jpg';
import wonderWomanIcon from '../assets/filter-icons/wonder-woman.jpg';
import xMenIcon from '../assets/filter-icons/x-men.jpg';

/**
 * The icon a character, team or series filter chip wears.
 *
 * Keys are the catalog's own tags: DC and Marvel are curated as "<who>-family",
 * while a manga line is tagged with its plain series slug. A tag with no entry
 * here renders as a plain text chip, which is the right answer whenever no icon
 * reads well at 20px.
 */
const ICON_BY_TAG: Record<string, string> = {
  // DC
  'bat-family': batmanIcon,
  'superman-family': supermanIcon,
  'wonder-woman-family': wonderWomanIcon,
  'lantern-family': greenLanternIcon,
  'flash-family': flashIcon,
  'justice-league-family': justiceLeagueIcon,
  // Marvel
  'spider-family': spiderManIcon,
  'avengers-family': avengersIcon,
  'x-men-family': xMenIcon,
  'fantastic-four-family': fantasticFourIcon,
  'wolverine-family': wolverineIcon,
  'street-level-family': daredevilIcon,
  'iron-man-family': ironManIcon,
  'thor-family': thorIcon,
  // Manga
  'jujutsu-kaisen': jjkIcon,
  'jojo-family': jojoIcon,
  'one-piece': onePieceIcon,
  'hunter-x-hunter': hunterXHunterIcon,
};

export function characterIcon(tag: string): string | undefined {
  return ICON_BY_TAG[tag];
}
