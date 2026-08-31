# SolidCog model scheduler

The scheduler runs inside WSL2 on port 8090 and keeps MinerU and MechVL mutually exclusive.

```bash
./setup.sh
./start.sh
```

It does not load a GPU model until `/switch/mineru`, `/switch/mechvl`, or a proxied model request is received.
