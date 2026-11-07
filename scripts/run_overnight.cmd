@echo off
chcp 65001 >nul
cd /d "E:\code\Working-on-it\项目"
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
set LOG=logs\overnight.log
echo ============================================ >> "%LOG%"
echo [start] %date% %time% >> "%LOG%"

echo [1/2] 消融 R3（大模型重排，200 题） >> "%LOG%"
python scripts\run_ablation.py --only R3 >> "%LOG%" 2>&1
echo [1/2 done] %date% %time% >> "%LOG%"

echo [2/2] 薄厚链路对比（完整 200 题） >> "%LOG%"
python scripts\compare_chains.py >> "%LOG%" 2>&1
echo [2/2 done] %date% %time% >> "%LOG%"

echo [all done] %date% %time% >> "%LOG%"
echo DONE > logs\overnight.done
