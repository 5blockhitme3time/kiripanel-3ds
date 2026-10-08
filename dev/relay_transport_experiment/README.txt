2026-10-04 的实验件，**未采用**。

背景：中继没在跑时，3DS 每 120 ms 的连接尝试会占住串行的 SOC 服务，把视频/音频的
socket 请求一起堵住 → 每 2 秒左右整个流冻 1.5 秒（用 `make DIAG=1` 编出的 diag.log 能看到）。

· http_client.hpp  自写的非阻塞 HTTP/1.1 客户端（等待全在 select() 里）。
· probe.hpp/.cpp   先单播探测中继是否在，收到应答才去连 TCP。
· beacon.py        配套的 PC 端 UDP 应答/广播（当时在 relay.py 里），test_beacon.py 是它的测试。

两者在 PC 上（对真 relay.py）都通过了回归，但装上 3DS 后面板一直连不上，
原因未查明（怀疑 libctru 的 select() 语义与 PC 不同）。已退回 curl 版本；
现在的缓解办法是中继连续连不上时拉长重试间隔（relay_client.cpp 的 kPollAbsentMs）。
若将来再试，先做的事：在 3DS 上单独验证 select() 对 TCP socket 的行为。
