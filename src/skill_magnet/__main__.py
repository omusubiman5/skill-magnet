from .cli import exit_process, main


exit_code = main()
exit_process(exit_code)
