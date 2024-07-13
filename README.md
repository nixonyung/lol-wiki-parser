# LoL Wiki Parser

Disclaimer: Intended for demo purposes only.

---

Parse champion stats from [League of Legends Wiki](https://leagueoflegends.fandom.com/wiki/League_of_Legends_Wiki), and dump the result to a local [minio](https://min.io/).

To run the app:

- run `docker build -t lol-wiki-parser .`
- run `docker run --rm -it lol-wiki-parser`
