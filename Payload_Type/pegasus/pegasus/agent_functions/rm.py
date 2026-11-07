from mythic_container.MythicCommandBase import *
from mythic_container.MythicRPC import *


class RmArguments(TaskArguments):
    def __init__(self, command_line, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="path",
                type=ParameterType.String,
                description="File or directory path to remove",
                parameter_group_info=[ParameterGroupInfo(required=True)],
            ),
            CommandParameter(
                name="recursive",
                type=ParameterType.Boolean,
                description="Recursively remove directories",
                default_value=False,
                parameter_group_info=[ParameterGroupInfo(required=False)],
            ),
        ]

    async def parse_arguments(self):
        if len(self.command_line) > 0 and self.command_line[0] == "{":
            self.load_args_from_json_string(self.command_line)
        elif len(self.command_line) > 0:
            self.add_arg("path", self.command_line)
        else:
            raise ValueError("Missing path argument")


class RmCommand(CommandBase):
    cmd = "rm"
    needs_admin = False
    help_cmd = "rm <path> [-r]"
    description = "Remove file or directory"
    version = 1
    author = "@Lavender-exe"
    argument_class = RmArguments
    attackmapping = ["T1070.004"]

    async def create_go_tasking(self, taskData: MythicCommandBase.PTTaskMessageAllData) -> MythicCommandBase.PTTaskCreateTaskingMessageResponse:
        response = MythicCommandBase.PTTaskCreateTaskingMessageResponse(
            TaskID=taskData.Task.ID,
            Success=True,
        )
        response.DisplayParams = taskData.args.get_arg("path")
        return response

    async def process_response(self, task: PTTaskMessageAllData, response: any) -> PTTaskProcessResponseMessageResponse:
        resp = PTTaskProcessResponseMessageResponse(TaskID=task.Task.ID, Success=True)
        return resp
