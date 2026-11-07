from mythic_container.MythicCommandBase import *
from mythic_container.MythicRPC import *


class KillArguments(TaskArguments):
    def __init__(self, command_line, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="pid",
                type=ParameterType.Number,
                description="Process ID to kill",
                parameter_group_info=[ParameterGroupInfo(required=True)],
            ),
        ]

    async def parse_arguments(self):
        if len(self.command_line) > 0 and self.command_line[0] == "{":
            self.load_args_from_json_string(self.command_line)
        elif len(self.command_line) > 0:
            self.add_arg("pid", int(self.command_line))
        else:
            raise ValueError("Missing PID argument")


class KillCommand(CommandBase):
    cmd = "kill"
    needs_admin = False
    help_cmd = "kill <pid>"
    description = "Kill a process by PID"
    version = 1
    author = "@Lavender-exe"
    argument_class = KillArguments
    attackmapping = ["T1489"]

    async def create_go_tasking(self, taskData: MythicCommandBase.PTTaskMessageAllData) -> MythicCommandBase.PTTaskCreateTaskingMessageResponse:
        response = MythicCommandBase.PTTaskCreateTaskingMessageResponse(
            TaskID=taskData.Task.ID,
            Success=True,
        )
        response.DisplayParams = str(taskData.args.get_arg("pid"))
        return response

    async def process_response(self, task: PTTaskMessageAllData, response: any) -> PTTaskProcessResponseMessageResponse:
        resp = PTTaskProcessResponseMessageResponse(TaskID=task.Task.ID, Success=True)
        return resp
