// Team and league logo utilities
// Uses ESPN's free CDN for logos (no API key needed, publicly accessible)

const ESPN_TEAM_LOGO = "https://a.espncdn.com/combiner/i?img=/i/teamlogos";
const API_FOOTBALL_LOGO = "https://media.api-sports.io/football/teams";
const API_FOOTBALL_LEAGUE = "https://media.api-sports.io/football/leagues";

// League logos (API-Football CDN - publicly accessible)
export const LEAGUE_LOGOS: Record<string, string> = {
  premier_league: `${API_FOOTBALL_LEAGUE}/39.png`,
  la_liga: `${API_FOOTBALL_LEAGUE}/140.png`,
  bundesliga: `${API_FOOTBALL_LEAGUE}/78.png`,
  serie_a: `${API_FOOTBALL_LEAGUE}/135.png`,
  ligue_1: `${API_FOOTBALL_LEAGUE}/61.png`,
  champions_league: `${API_FOOTBALL_LEAGUE}/2.png`,
  mls: `${API_FOOTBALL_LEAGUE}/253.png`,
  fa_cup: `${API_FOOTBALL_LEAGUE}/45.png`,
  nba: "https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500/nba.png&w=32&h=32",
  nhl: "https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500/nhl.png&w=32&h=32",
  ipl: "https://a.espncdn.com/combiner/i?img=/i/cricket/cricinfoimage/ipl-logo.png&w=32&h=32",
};

// Football team ID mapping (API-Football IDs)
const FOOTBALL_TEAM_IDS: Record<string, number> = {
  // Premier League
  "Arsenal": 42, "Aston Villa": 66, "AFC Bournemouth": 35, "Bournemouth": 35,
  "Brentford": 55, "Brighton": 51, "Brighton and Hove Albion": 51,
  "Burnley": 44, "Chelsea": 49, "Crystal Palace": 52, "Everton": 45,
  "Fulham": 36, "Leeds United": 63, "Liverpool": 40, "Manchester City": 50,
  "Manchester United": 33, "Newcastle United": 34, "Newcastle": 34,
  "Nottingham Forest": 65, "Sunderland": 71,
  "Tottenham Hotspur": 47, "Tottenham": 47, "West Ham United": 48, "West Ham": 48,
  "Wolverhampton Wanderers": 39, "Wolves": 39,

  // La Liga
  "Real Madrid": 541, "Barcelona": 529, "Atletico Madrid": 530, "Atlético Madrid": 530,
  "Sevilla": 536, "Real Sociedad": 548, "Villarreal": 533, "Athletic Club": 531,
  "Real Betis": 543, "Girona": 547, "Valencia": 532, "Celta Vigo": 538,
  "Mallorca": 798, "RCD Mallorca": 798, "Getafe": 546, "Espanyol": 540,
  "Osasuna": 727, "Las Palmas": 534, "Rayo Vallecano": 728, "Alaves": 542,
  "Valladolid": 720, "Leganes": 539, "Elche": 797,

  // Bundesliga
  "Bayern Munich": 157, "Bayern München": 157, "FC Bayern München": 157,
  "Borussia Dortmund": 165, "Bayer Leverkusen": 168, "RB Leipzig": 173,
  "VfB Stuttgart": 172, "SC Freiburg": 160, "Freiburg": 160,
  "Eintracht Frankfurt": 169, "VfL Wolfsburg": 161, "Wolfsburg": 161,
  "Borussia Mönchengladbach": 163, "1. FC Union Berlin": 182, "Union Berlin": 182,
  "TSG Hoffenheim": 167, "Hoffenheim": 167, "FC Augsburg": 170, "Augsburg": 170,
  "1. FC Heidenheim": 180, "Heidenheim": 180, "SV Werder Bremen": 162, "Werder Bremen": 162,
  "Holstein Kiel": 191, "FC St. Pauli": 186, "St. Pauli": 186, "St Pauli": 186,
  "1. FC Köln": 159, "Köln": 159, "Mainz 05": 164, "Mainz": 164,
  "Hamburger SV": 166, "Hamburg": 166,

  // Serie A
  "Inter": 505, "Internazionale": 505, "AC Milan": 489, "Milan": 489,
  "Juventus": 496, "Napoli": 492, "AS Roma": 497, "Roma": 497,
  "Lazio": 487, "Atalanta": 499, "Fiorentina": 502,
  "Bologna": 500, "Torino": 503, "Udinese": 494,
  "Monza": 1579, "Cagliari": 490, "Sassuolo": 488,
  "Parma": 495, "Empoli": 511, "Verona": 504,
  "Pisa": 514, "Lecce": 867, "Venezia": 517,
  "Como": 895, "Genoa": 491, "Salernitana": 514,

  // Ligue 1
  "Paris Saint Germain": 85, "Paris Saint-Germain": 85, "PSG": 85,
  "Marseille": 81, "AS Monaco": 91, "Monaco": 91,
  "Lyon": 80, "Lille": 79, "Nice": 84,
  "Lens": 116, "Rennes": 94, "Strasbourg": 95,
  "Toulouse": 96, "Nantes": 83, "Montpellier": 82,
  "Brest": 106, "Reims": 93, "Le Havre": 109,
  "Metz": 112, "Auxerre": 97, "Angers": 77,
  "Saint-Etienne": 1063, "Paris FC": 1064,

  // MLS
  "Inter Miami CF": 9568, "Inter Miami": 9568,
  "New York Red Bulls": 1602, "NY Red Bulls": 1602,
  "LAFC": 1599, "Los Angeles FC": 1599,
  "LA Galaxy": 1601, "Atlanta United FC": 1604, "Atlanta United": 1604,
  "Austin FC": 9565, "Nashville SC": 9569,
  "New York City FC": 9589, "Columbus Crew": 1596,
  "Cincinnati": 9561, "FC Cincinnati": 9561,
  "Orlando City SC": 1598, "Orlando City": 1598,
  "Colorado Rapids": 1600, "Portland Timbers": 1608,
  "Seattle Sounders FC": 1595, "Seattle Sounders": 1595,

  // Champions League extras
  "Sporting CP": 228, "Sporting Lisbon": 228,
};

// NBA teams (ESPN IDs)
const NBA_TEAM_IDS: Record<string, string> = {
  "Atlanta Hawks": "atl", "Boston Celtics": "bos", "Brooklyn Nets": "bkn",
  "Charlotte Hornets": "cha", "Chicago Bulls": "chi", "Cleveland Cavaliers": "cle",
  "Dallas Mavericks": "dal", "Denver Nuggets": "den", "Detroit Pistons": "det",
  "Golden State Warriors": "gs", "Houston Rockets": "hou", "Indiana Pacers": "ind",
  "LA Clippers": "lac", "Los Angeles Clippers": "lac",
  "Los Angeles Lakers": "lal", "LA Lakers": "lal",
  "Memphis Grizzlies": "mem", "Miami Heat": "mia", "Milwaukee Bucks": "mil",
  "Minnesota Timberwolves": "min", "New Orleans Pelicans": "no",
  "New York Knicks": "ny", "Oklahoma City Thunder": "okc",
  "Orlando Magic": "orl", "Philadelphia 76ers": "phi", "Phoenix Suns": "phx",
  "Portland Trail Blazers": "por", "Sacramento Kings": "sac",
  "San Antonio Spurs": "sa", "Toronto Raptors": "tor",
  "Utah Jazz": "uta", "Washington Wizards": "wsh",
};

// NHL teams (ESPN IDs)
const NHL_TEAM_IDS: Record<string, string> = {
  "Anaheim Ducks": "ana", "Arizona Coyotes": "ari", "Boston Bruins": "bos",
  "Buffalo Sabres": "buf", "Calgary Flames": "cgy", "Carolina Hurricanes": "car",
  "Chicago Blackhawks": "chi", "Colorado Avalanche": "col",
  "Columbus Blue Jackets": "cbj", "Dallas Stars": "dal",
  "Detroit Red Wings": "det", "Edmonton Oilers": "edm",
  "Florida Panthers": "fla", "Los Angeles Kings": "la",
  "Minnesota Wild": "min", "Montreal Canadiens": "mtl",
  "Nashville Predators": "nsh", "New Jersey Devils": "nj",
  "New York Islanders": "nyi", "New York Rangers": "nyr",
  "Ottawa Senators": "ott", "Philadelphia Flyers": "phi",
  "Pittsburgh Penguins": "pit", "San Jose Sharks": "sj",
  "Seattle Kraken": "sea", "St Louis Blues": "stl", "St. Louis Blues": "stl",
  "Tampa Bay Lightning": "tb", "Toronto Maple Leafs": "tor",
  "Utah Hockey Club": "uta", "Vancouver Canucks": "van",
  "Vegas Golden Knights": "vgk", "Washington Capitals": "wsh",
  "Winnipeg Jets": "wpg",
};

// IPL teams
const IPL_LOGOS: Record<string, string> = {
  "Chennai Super Kings": "https://bcciplayerimages.s3.ap-south-1.amazonaws.com/ipl/CSK/logos/Roundbig/CSKroundbig.png",
  "Mumbai Indians": "https://bcciplayerimages.s3.ap-south-1.amazonaws.com/ipl/MI/logos/Roundbig/MIroundbig.png",
  "Royal Challengers Bangalore": "https://bcciplayerimages.s3.ap-south-1.amazonaws.com/ipl/RCB/logos/Roundbig/RCBroundbig.png",
  "Royal Challengers Bengaluru": "https://bcciplayerimages.s3.ap-south-1.amazonaws.com/ipl/RCB/logos/Roundbig/RCBroundbig.png",
  "Kolkata Knight Riders": "https://bcciplayerimages.s3.ap-south-1.amazonaws.com/ipl/KKR/logos/Roundbig/KKRroundbig.png",
  "Delhi Capitals": "https://bcciplayerimages.s3.ap-south-1.amazonaws.com/ipl/DC/logos/Roundbig/DCroundbig.png",
  "Punjab Kings": "https://bcciplayerimages.s3.ap-south-1.amazonaws.com/ipl/PBKS/logos/Roundbig/PBKSroundbig.png",
  "Rajasthan Royals": "https://bcciplayerimages.s3.ap-south-1.amazonaws.com/ipl/RR/logos/Roundbig/RRroundbig.png",
  "Sunrisers Hyderabad": "https://bcciplayerimages.s3.ap-south-1.amazonaws.com/ipl/SRH/logos/Roundbig/SRHroundbig.png",
  "Gujarat Titans": "https://bcciplayerimages.s3.ap-south-1.amazonaws.com/ipl/GT/logos/Roundbig/GTroundbig.png",
  "Lucknow Super Giants": "https://bcciplayerimages.s3.ap-south-1.amazonaws.com/ipl/LSG/logos/Roundbig/LSGroundbig.png",
};

export function getTeamLogo(teamName: string, sport: string): string {
  // IPL
  if (sport === "ipl" || sport === "cricket_ipl") {
    const iplLogo = Object.entries(IPL_LOGOS).find(([key]) =>
      teamName.toLowerCase().includes(key.toLowerCase()) || key.toLowerCase().includes(teamName.toLowerCase())
    );
    if (iplLogo) return iplLogo[1];
  }

  // NBA
  if (sport === "nba" || sport === "basketball_nba") {
    const nbaId = Object.entries(NBA_TEAM_IDS).find(([key]) =>
      teamName.toLowerCase().includes(key.toLowerCase()) || key.toLowerCase().includes(teamName.toLowerCase())
    );
    if (nbaId) return `https://a.espncdn.com/combiner/i?img=/i/teamlogos/nba/500/${nbaId[1]}.png&w=40&h=40`;
  }

  // NHL
  if (sport === "nhl" || sport === "icehockey_nhl") {
    const nhlId = Object.entries(NHL_TEAM_IDS).find(([key]) =>
      teamName.toLowerCase().includes(key.toLowerCase()) || key.toLowerCase().includes(teamName.toLowerCase())
    );
    if (nhlId) return `https://a.espncdn.com/combiner/i?img=/i/teamlogos/nhl/500/${nhlId[1]}.png&w=40&h=40`;
  }

  // Football (API-Football CDN)
  const footballId = Object.entries(FOOTBALL_TEAM_IDS).find(([key]) =>
    teamName.toLowerCase() === key.toLowerCase() || teamName.toLowerCase().includes(key.toLowerCase()) || key.toLowerCase().includes(teamName.toLowerCase())
  );
  if (footballId) return `${API_FOOTBALL_LOGO}/${footballId[1]}.png`;

  // Fallback
  return "";
}

export function getLeagueLogo(sport: string): string {
  return LEAGUE_LOGOS[sport] || "";
}
