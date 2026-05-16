set script_name=%~n0
set script_dir=%~dp0
set parent_dir=%~dp0..\

set PYTHONPATH=%parent_dir%;%PYTHONPATH%
set GHCONFIG=%parent_dir%\ghconfig.ini

python %script_dir%/%script_name%.py
