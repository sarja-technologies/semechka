# Staging acceptance (run ON the Spark)

Real API, real GPU, real router; credentials come from `~/semechka-deploy/secrets/staging-credentials.txt` and are never printed.

    ~/spark-reset/gpu-llm   python3 acc_chat.py        # streamed chat, multi-turn context, history, cross-org 404
    ~/spark-reset/gpu-llm   python3 acc_conc.py        # two people chatting at once on two models: both answer, neither evicted
    ~/spark-reset/gpu-media python3 acc_media.py       # Qwen-Image via the engine; MiniMax H3 text-to-video 5 s (ffprobe length check)
    ~/spark-reset/gpu-media python3 acc_rea.py         # Reverse Engineer Video through Semechka
    python3 acc_work_setup.py                           # enable Work Mode on staging (then scripts/spark/goose_e2e.py for the run)

`../post_verify.py <instance>` proves, on a COPY of a live instance's data, that every existing account signs in and sees its own
conversations and (decrypted) media; `VERIFY_WORK=1` also checks Work Mode readiness from that instance's own settings.
Output is review material: LOOK at the frames (contact sheet) of any video before calling it good.
